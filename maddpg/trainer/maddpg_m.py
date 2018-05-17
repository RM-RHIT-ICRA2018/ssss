import numpy as np
import random
import tensorflow as tf
import maddpg.common.tf_util as U

import pdb

from maddpg.common.distributions import make_pdtype
from maddpg import AgentTrainer
from maddpg.trainer.replay_buffer import ReplayBuffer


def discount_with_dones(rewards, dones, gamma):
    discounted = []
    r = 0
    for reward, done in zip(rewards[::-1], dones[::-1]):
        r = reward + gamma*r
        r = r*(1.-done)
        discounted.append(r)
    return discounted[::-1]

def make_update_exp(vals, target_vals):
    polyak = 1.0 - 1e-2
    expression = []
    for var, var_target in zip(sorted(vals, key=lambda v: v.name), sorted(target_vals, key=lambda v: v.name)):
        expression.append(var_target.assign(polyak * var_target + (1.0-polyak) * var))
    expression = tf.group(*expression)
    return U.function([], [], updates=[expression])

def p_train(make_obs_ph_n, act_space_n, p_index, p_func, q_func, shared_CNN,optimizer, make_obs_map_ph_n, grad_norm_clipping=None, local_q_func=False, num_units=64, scope="trainer", reuse=None):
    act_pdtype_n = [make_pdtype(act_space) for act_space in act_space_n]

    # set up placeholders
    obs_ph_n = make_obs_ph_n
    act_ph_n = [act_pdtype_n[i].sample_placeholder([None], name="action"+str(i)) for i in range(len(act_space_n))]

    obs_map_ph_n=make_obs_map_ph_n
    p_input = obs_ph_n[p_index]
    p_map_input=obs_map_ph_n[p_index]


    
    with tf.variable_scope(scope, reuse=None):
        # create distribtuions
        

        map_context_input=shared_CNN(p_map_input,p_index,scope="CNN")
        CNN_vars=U.scope_vars(U.absolute_scope_name("CNN"))
        # num_adversary=2
        # if p_index<num_adversary:
        #     map_context_input=shared_CNN(p_map_input,scope="CNN-adv")
        #     CNN_vars=U.scope_vars("CNN-adv")
        # else:
        #     map_context_input=shared_CNN(p_map_input,scope="CNN-age")
        #     CNN_vars=U.scope_vars("CNN-age")
        p = p_func(tf.concat([p_input,map_context_input],1), int(act_pdtype_n[p_index].param_shape()[0]), scope="p_func", num_units=num_units)
        p_func_vars = U.scope_vars(U.absolute_scope_name("p_func"))
        

        # wrap parameters in distribution
        act_pd = act_pdtype_n[p_index].pdfromflat(p)

        act_sample = act_pd.sample()
        p_reg = tf.reduce_mean(tf.square(act_pd.flatparam()))

        act_input_n = act_ph_n + []
        act_input_n[p_index] = act_pd.sample() #act_pd.mode() #
        q_input = tf.concat(obs_ph_n + act_input_n, 1)
    
    for i in range(len(obs_ph_n)):
        q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],i,scope="agent_"+str(i)+"/CNN")],1)
        # for i in range(len(obs_ph_n)):
        #     if i<num_adversary:
        #         q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],scope="CNN-adv")],1)
        #     else:
        #         q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],scope="CNN-age")],1)

    # for i in range(len(obs_ph_n)):
    #     q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],i,scope="agent_"+str(i)+"/CNN")],1)
    
    # with tf.variable_scope(scope, reuse=None):
    
    with tf.variable_scope(scope, reuse=None):
        if local_q_func:
            q_input = tf.concat([obs_ph_n[p_index], act_input_n[p_index]], 1)
        q = q_func(q_input, 1, scope="q_func", reuse=True, num_units=num_units)[:,0]

        pg_loss = -tf.reduce_mean(q)

        loss = pg_loss + p_reg * 1e-3

        optimize_expr = U.minimize_and_clip(optimizer, loss, p_func_vars, grad_norm_clipping)
    
    with tf.variable_scope(scope, reuse=True):
        optimize_expr2 = U.minimize_and_clip(optimizer, loss, CNN_vars, grad_norm_clipping)
        # Create callable functions
    with tf.variable_scope(scope, reuse=None):
        train = U.function(inputs=obs_ph_n + act_ph_n+obs_map_ph_n, outputs=loss, updates=[optimize_expr,optimize_expr2])
        act = U.function(inputs=[obs_ph_n[p_index],obs_map_ph_n[p_index]], outputs=act_sample)
        p_values = U.function([obs_ph_n[p_index],obs_map_ph_n[p_index]], p)
        #p_values = U.function([obs_ph_n[p_index]], p)

        # target network
        target_p = p_func(tf.concat([p_input,map_context_input],1), int(act_pdtype_n[p_index].param_shape()[0]), scope="target_p_func", num_units=num_units)
        target_p_func_vars = U.scope_vars(U.absolute_scope_name("target_p_func"))
        update_target_p = make_update_exp(p_func_vars, target_p_func_vars)

        target_act_sample = act_pdtype_n[p_index].pdfromflat(target_p).sample()
        target_act = U.function(inputs=[obs_ph_n[p_index],obs_map_ph_n[p_index]], outputs=target_act_sample)

        return act, train, update_target_p, {'p_values': p_values, 'target_act': target_act}

def q_train(make_obs_ph_n, act_space_n, q_index, q_func, shared_CNN,optimizer, make_obs_map_ph_n, grad_norm_clipping=None, local_q_func=False, scope="trainer", reuse=None, num_units=64):
    # with tf.variable_scope(scope, reuse=reuse):
    # create distribtuions
    act_pdtype_n = [make_pdtype(act_space) for act_space in act_space_n]

    # set up placeholders
    obs_ph_n = make_obs_ph_n
    obs_map_ph_n=make_obs_map_ph_n
    act_ph_n = [act_pdtype_n[i].sample_placeholder([None], name="action"+str(i)) for i in range(len(act_space_n))]
    target_ph = tf.placeholder(tf.float32, [None], name="target")

    q_input = tf.concat(obs_ph_n + act_ph_n, 1)
    # num_adversary=2
    # for i in range(len(obs_ph_n)):
    #     if i<num_adversary:
    #         q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],scope="CNN-adv")],1)
    #     else:
    #         q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],scope="CNN-age")],1)
    for i in range(len(obs_ph_n)):
        q_input=tf.concat([q_input,shared_CNN(obs_map_ph_n[i],i,scope="agent_"+str(i)+"/CNN")],1)

    with tf.variable_scope(scope, reuse=reuse):
        if local_q_func:
            q_input = tf.concat([obs_ph_n[q_index], act_ph_n[q_index]], 1)
        q = q_func(q_input, 1, scope="q_func", num_units=num_units)[:,0]
        q_func_vars = U.scope_vars(U.absolute_scope_name("q_func"))
        CNN_vars=U.scope_vars(U.absolute_scope_name("CNN"))
        #CNN_age_vars=U.scope_vars("CNN-age")

        q_loss = tf.reduce_mean(tf.square(q - target_ph))

        # viscosity solution to Bellman differential equation in place of an initial condition
        q_reg = tf.reduce_mean(tf.square(q))
        loss = q_loss #+ 1e-3 * q_reg

        optimize_expr = U.minimize_and_clip(optimizer, loss, q_func_vars+CNN_vars, grad_norm_clipping)

        # Create callable functions
        train = U.function(inputs=obs_ph_n + obs_map_ph_n+act_ph_n +[target_ph], outputs=loss, updates=[optimize_expr])
        q_values = U.function(obs_ph_n +obs_map_ph_n+act_ph_n, q)

        # target network
        target_q = q_func(q_input, 1, scope="target_q_func", num_units=num_units)[:,0]
        target_q_func_vars = U.scope_vars(U.absolute_scope_name("target_q_func"))
        update_target_q = make_update_exp(q_func_vars, target_q_func_vars)

        target_q_values = U.function(obs_ph_n+obs_map_ph_n+act_ph_n, target_q)


        return train, update_target_q, {'q_values': q_values, 'target_q_values': target_q_values}

class MADDPGAgentTrainer(AgentTrainer):
    def __init__(self, name, model, CNN_model,obs_shape_n, obs_map_shape_n, act_space_n, agent_index, args, local_q_func=False):
        self.name = name
        self.n = len(obs_shape_n)
        self.agent_index = agent_index
        self.args = args
        obs_ph_n = []
        obs_map_ph_n=[]
        for i in range(self.n):
            obs_ph_n.append(U.BatchInput(obs_shape_n[i], name="observation"+str(i)).get())
            obs_map_ph_n.append(U.BatchInput(obs_map_shape_n[i], name="observation_map"+str(i)).get())
        # Create all the functions necessary to train the model
        self.q_train, self.q_update, self.q_debug = q_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            q_index=agent_index,
            q_func=model,
            shared_CNN=CNN_model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units,
            make_obs_map_ph_n=obs_map_ph_n

        )
        self.act, self.p_train, self.p_update, self.p_debug = p_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            p_index=agent_index,
            p_func=model,
            q_func=model,
            shared_CNN=CNN_model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units,
            make_obs_map_ph_n=obs_map_ph_n
        )
        # Create experience buffer
        self.replay_buffer = ReplayBuffer(1e6)
        self.max_replay_buffer_len = args.batch_size * args.max_episode_len//10
        self.batch_size=args.batch_size
        self.replay_sample_index = None

    def action(self, obs):
        return self.act([obs[0]],[obs[1]])[0]

    def experience(self, obs, act, rew, new_obs, done, terminal):
        # Store transition in the replay buffer.
        self.replay_buffer.add(obs, act, rew, new_obs, float(done))

    def preupdate(self):
        self.replay_sample_index = None

    def update(self, agents, t):
        if len(self.replay_buffer) < self.max_replay_buffer_len: # replay buffer is not large enough
            return
        if not t % 100 == 0:  # only update every 100 steps
            return

        self.replay_sample_index = self.replay_buffer.make_index(self.args.batch_size)
        # collect replay sample from all agents
        obs_n = []
        obs_map_n=[]
        obs_next_map=[]
        obs_next_n = []
        act_n = []
        index = self.replay_sample_index
        for i in range(self.n):
            obs, act, rew, obs_next, done = agents[i].replay_buffer.sample_index(index)

            #pdb.set_trace()
            obs_n.append(obs[:,0].tolist())
            obs_next_n.append(obs_next[:,0].tolist())
            obs_map_n.append(obs[:,1].tolist())
            obs_next_map.append(obs_next[:,1].tolist())
            act_n.append(act)
        

        obs, act, rew, obs_next, done = self.replay_buffer.sample_index(index)

        # train q network
        num_sample = 1
        target_q = 0.0

        for ii in range(num_sample):
            # target_act_next_n=[]
            # for i in raneg(env.n):
            #     for j in range(len(index)):

            #     target_act_next_n.append()
            #target_act_next_n = [[agents[i].p_debug['target_act']([obs_next_n[i][j]],[obs_next_map[i][j]]) for j in range(len(index))] for i in range(self.n)]
            target_act_next_n = [np.array([np.reshape(np.array(agents[i].p_debug['target_act']([obs_next_n[i][j]],[obs_next_map[i][j]])),-1) for j in range(self.batch_size)]) for i in range(self.n)]
            #target_act_next_n = np.reshape(target_act_next_n,[self.n,len(index)])
            #obs_next_n=np.reshape(obs_next_n,[self.n,len(index),-1])
            #pdb.set_trace()
            target_q_next = self.q_debug['target_q_values'](*(obs_next_n+obs_next_map+target_act_next_n))
            target_q += rew + self.args.gamma * (1.0 - done) * target_q_next
        target_q /= num_sample
        q_loss = self.q_train(*(obs_n + obs_map_n+act_n+[target_q]))


        # train p network
        p_loss = self.p_train(*(obs_n + act_n+obs_map_n))

        self.p_update()
        self.q_update()

        return [q_loss, p_loss, np.mean(target_q), np.mean(rew), np.mean(target_q_next), np.std(target_q)]
