#!/usr/bin/env/python

from typing import Tuple, List, Any, Sequence

import tensorflow as tf
import time
import os
import json
import numpy as np
import pickle
import random

from MLP import MLP
from inits import SMALL_NUMBER
from ThreadedIterator import  ThreadIterator


def get_ont_hot(node_features) -> list:
    """
    图中节点的ont-hot编码
    :param node_features: valid.json中的node_features属性值
    :return:
    """
    nodes = []
    for idx in node_features:
        z = [0 for _ in range(190)]
        z[idx] = 1
        nodes.append(z)
    return nodes


class BaseModel(object):

    """
    模型参数设置
    """
    @classmethod
    def default_params(cls):
        return {
            'num_epochs': 3000,
            'patience': 25,
            'learning_rate': 0.001,
            'clamp_gradient_norm': 1.0,
            'out_layer_dropout_keep_prob': 1.0,

            'hidden_size': 190,  # 190 200  220  250  300
            'num_timesteps': 4,
            'use_graph': True,

            'tie_fwd_bkwd':True,
            # 'task_ids': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            'task_ids': [1003, 1001, 1002, 1000, 1004, 1007, 1009, 1021, 1022, 1030,
                         1087, 1100, 1101, 1104, 1111, 1112, 1115, 1120, 1160, 1178,
                         1202, 1212, 1216, 1261, 1330, 1334, 1335, 1337, 1406, 1408],
            'random_seed': 0,

            'train_file': 'train_graphs.json',
            'valid_file': 'valid_graphs.json'
        }

    def __init__(self, args):
        self.args = args

        # 从命令行中收集文件路径参数信息
        data_dir = ''   # 存放数据文件的文件夹
        if '--data_dir' in args and args['--data_dir'] is not None:
            data_dir = args['--data_dir']
        self.data_dir = data_dir
        # 线程id名称
        self.run_id = "_".join([time.strftime("%Y-%m-%d-%H-%M-%S"), str(os.getpid())])
        # 日志文件存放路径
        log_dir = args.get('--log_dir') or '.'
        self.log_file = os.path.join(log_dir, "%s_log.json" % self.run_id)
        # 效果最好的模型参数文件
        self.best_model_file = os.path.join(log_dir, "%s_model_best.pickle" % self.run_id)

        # 从命令行收集模型参数配置文件信息
        params = self.default_params()
        config_file = args.get('--config-file')
        if config_file is not None:
            with open(config_file, 'r') as f:
                params.update(json.load(f))
        config = args.get('--config')
        if config is not None:
            params.update(json.loads(config))
        self.params = params

        # 将配置好的模型参数保存到参数文件
        with open(os.path.join(log_dir, "%s_params.json" % self.run_id), "w") as f:
            json.dump(params, f)
        print("Run %s starting with following parameters:\n%s" % (self.run_id, json.dumps(self.params)))

        # 设置模型随机数seed
        random.seed(params['random_seed'])
        np.random.seed(params['random_seed'])

        # 加载数据
        self.max_num_vertices = 0
        self.num_edge_types = 0
        self.annotation_size = 0
        self.train_data = self.load_data(params['train_file'], is_training_data=True)
        self.valid_data = self.load_data(params['valid_file'], is_training_data=False)

        # 构建模型
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        self.graph = tf.Graph()
        self.sess = tf.Session(graph=self.graph, config=config)
        with self.graph.as_default():
            tf.set_random_seed(params['random_seed'])
            self.placeholders = {}
            self.weights = {}
            self.ops = {}
            self.make_model()
            self.make_train_step()

            # 保存/初始化模型变量
            restore_file = args.get('--restore')
            if restore_file is not None:
                self.restore_model(restore_file)
            else:
                self.initialize_model()

    def load_data(self, file_name, is_training_data: bool):
        """
           加载数据，统计边类型信息，顶点数目信息，annotation_size信息
        :param file_name:
        :param is_training_data:
        :return:
        """
        full_path = os.path.join(self.data_dir, file_name)

        print("Loading data from %s" % full_path)
        with open(full_path, 'r',encoding='utf-8') as f:
            data = json.load(f)

        restrict = self.args.get("--restrict_data")
        if restrict is not None and restrict > 0:
            data = data[:restrict]

        # 统计数据中一些信息
        num_fwd_edge_types = 0
        for i, g in enumerate(data):
            # 图中顶点的ont-hot编码
            data[i]['node_features'] = get_ont_hot(g['node_features'])
            # 最大顶点编号
            self.max_num_vertices = max(self.max_num_vertices, max([v for e in g['graph'] for v in [e[0], e[2]]]))
            # 边的种类数目/最大边标签编号
            num_fwd_edge_types = max(self.num_edge_types, max([e[1] for e in g['graph']]))
        self.num_edge_types = max(self.num_edge_types, num_fwd_edge_types * (1 if self.params['tie_fwd_bkwd'] else 2))
        # annotation_size
        self.annotation_size = max(self.annotation_size, len(data[0]["node_features"][0]))

        return self.process_raw_graphs(data, is_training_data)

    @staticmethod
    def graph_string_to_array(graph_string: str) -> List[List[int]]:
        return [[int(v) for v in s.split(' ')]
                for s in graph_string.split('\n')]

    def process_raw_graphs(self, raw_data: Sequence[Any], is_training_data: bool) -> Any:
        raise Exception("Models have to implement process_raw_graphs!")

    def make_model(self):
        self.placeholders['target_values'] = tf.placeholder(tf.float32, [len(self.params['task_ids']), None],
                                                            name='target_values')
        self.placeholders['target_mask'] = tf.placeholder(tf.float32, [len(self.params['task_ids']), None],
                                                          name='target_mask')
        self.placeholders['num_graphs'] = tf.placeholder(tf.int64, [], name='num_graphs')
        self.placeholders['out_layer_dropout_keep_prob'] = tf.placeholder(tf.float32, [],
                                                                          name='out_layer_dropout_keep_prob')

        with tf.variable_scope("graph_model"):
            self.prepare_specific_graph_model()
            # 开始构建graph work
            if self.params['use_graph']:
                self.ops['final_node_representations'] = self.compute_final_node_representations()
            else:
                self.ops['final_node_representations'] = tf.zeros_like(self.placeholders['initial_node_representation'])

        self.ops['losses'] = []

        # 针对每个任务
        for (internal_id, task_id) in enumerate(self.params['task_ids']):
            with tf.variable_scope("out_layer_task%i" % task_id):
                with tf.variable_scope("regression_gate"):
                    """
                        增加全连接层 + relu
                        regression_gate_task_i:  (输入2*h_dim,  输出1)
                        regression_transform_task_i:   (输入h_dim,输出1)
                    """
                    self.weights['regression_gate_task%i' % task_id] = MLP(2 * self.params['hidden_size'], 1, [],
                                                                           self.placeholders[
                                                                               'out_layer_dropout_keep_prob'])
                with tf.variable_scope("regression"):
                    self.weights['regression_transform_task%i' % task_id] = MLP(self.params['hidden_size'], 1, [],
                                                                                self.placeholders[
                                                                                    'out_layer_dropout_keep_prob'])
                computed_values, sigmoid_node_in_graph = self.gated_regression(self.ops['final_node_representations'],
                                                         self.weights['regression_gate_task%i' % task_id],
                                                         self.weights['regression_transform_task%i' % task_id])

                diff = computed_values - self.placeholders['target_values'][internal_id, :]
                task_target_mask = self.placeholders['target_mask'][internal_id, :]
                task_target_num = tf.reduce_sum(task_target_mask) + SMALL_NUMBER
                # Make out unused values
                diff = diff * task_target_mask
                self.ops['accuracy_task%i' % task_id] = tf.reduce_sum(tf.abs(tf.round(tf.abs(diff)) - 1))

                task_loss = tf.reduce_sum(0.5 * tf.square(diff)) / task_target_num
                # Normalise loss to account for fewer task-specific examples in batch:
                task_loss = task_loss * (1.0 / (self.params['task_sample_ratios'].get(task_id) or 1.0))
                self.ops['losses'].append(task_loss)
                self.ops['sigmoid_node_in_graph'] = sigmoid_node_in_graph
        self.ops['loss'] = tf.reduce_sum(self.ops['losses'])

    def make_train_step(self):
        trainable_vars = self.sess.graph.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)
        if self.args.get('--freeze-graph-model'):
            graph_vars = set(self.sess.graph.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope="graph_model"))
            filtered_vars = []
            for var in trainable_vars:
                if var not in graph_vars:
                    filtered_vars.append(var)
                else:
                    print("Freezing weights of variable %s." % var.name)
            trainable_vars = filtered_vars
        optimizer = tf.train.AdamOptimizer(self.params['learning_rate'])
        grads_and_vars = optimizer.compute_gradients(self.ops['loss'], var_list=trainable_vars)
        clipped_grads = []
        for grad, var in grads_and_vars:
            if grad is not None:
                clipped_grads.append((tf.clip_by_norm(grad, self.params['clamp_gradient_norm']), var))
            else:
                clipped_grads.append((grad, var))
        self.ops['train_step'] = optimizer.apply_gradients(clipped_grads)
        # Initialize newly-introduced variables:
        self.sess.run(tf.local_variables_initializer())

    def gated_regression(self, last_h, regression_gate, regression_transform):
        raise Exception("Models have to implement gated_regression!")

    def prepare_specific_graph_model(self) -> None:
        raise Exception("Models have to implement prepare_specific_graph_model!")

    def compute_final_node_representations(self) -> tf.Tensor:
        raise Exception("Models have to implement compute_final_node_representations!")

    def make_minibatch_iterator(self, data: Any, is_training: bool):
        raise Exception("Models have to implement make_minibatch_iterator!")

    def run_epoch(self, epoch_name: str, data, is_training: bool):

        loss = 0
        accuracies = np.zeros(shape=(len(self.params['task_ids']),))
        accuracy_ops = [self.ops['accuracy_task%i' % task_id] for task_id in self.params['task_ids']]
        start_time = time.time()
        processed_graphs = 0
        batch_iterator = ThreadIterator(self.make_minibatch_iterator(data, is_training), max_queue_size=5)
        for step, batch_data in enumerate(batch_iterator):
            num_graphs = batch_data[self.placeholders['num_graphs']]
            processed_graphs += num_graphs
            if is_training:
                batch_data[self.placeholders['out_layer_dropout_keep_prob']] = self.params[
                    'out_layer_dropout_keep_prob']
                fetch_list = [self.ops['loss'], accuracy_ops, self.ops['train_step']]
            else:
                batch_data[self.placeholders['out_layer_dropout_keep_prob']] = 1.0
                fetch_list = [self.ops['loss'], accuracy_ops]

            fetch_list.append(self.ops['final_node_representations'])
            fetch_list.append(self.ops['sigmoid_node_in_graph'])

            result = self.sess.run(fetch_list, feed_dict=batch_data)

            (batch_loss, batch_accuracies, final_node_representations, sigmoid_in_graph) = (result[0], result[1], result[2], result[3])

            loss += batch_loss * num_graphs
            accuracies += batch_accuracies
            print("Running %s, batch %i (has %i graphs). Loss so far: %.4f" % (epoch_name,
                                                                               step,
                                                                               num_graphs,
                                                                               loss / processed_graphs),
                  end='\r')

        accuracies = accuracies / processed_graphs
        loss = loss / processed_graphs
        # error_ratios = accuracies / chemical_accuracies[self.params["task_ids"]]
        instance_per_sec = processed_graphs / (time.time() - start_time)
        return loss, accuracies, instance_per_sec, final_node_representations, sigmoid_in_graph

    def train(self):
        log_to_save = []
        total_time_start = time.time()
        with self.graph.as_default():
            if self.args.get('--restore') is not None:
                _, valid_accs, _, final_nodes_representations, sigmoid_in_graph = self.run_epoch("Resumed (validation)", self.valid_data, False)
                best_val_acc = np.sum(valid_accs)
                best_val_acc_epoch = 0
                print("\r\x1b[KResumed operation, initial cum. val. acc: %.5f" % best_val_acc)
            else:
                (best_val_acc, best_val_acc_epoch) = (float("+inf"), 0)
            for epoch in range(1, self.params['num_epochs'] + 1):
                print("== Epoch %i" % epoch)
                train_loss, train_accs, train_speed, train_final_nodes_representations, train_sigmoid_in_graph = self.run_epoch("epoch %i (training)" % epoch,
                                                                     self.train_data, True)
                print("compare")
                accs_str = " ".join(["%i:%.5f" % (id, acc) for (id, acc) in zip(self.params['task_ids'], train_accs)])
                # errs_str = " ".join(["%i:%.5f" % (id, err) for (id, err) in zip(self.params['task_ids'], train_errs)])
                print("\r\x1b[K Train: loss: %.5f | acc: %s | instances/sec: %.2f" % (train_loss,
                                                                                      accs_str,
                                                                                      train_speed))
                valid_loss, valid_accs, valid_speed, final_nodes_representations, sigmoid_in_graph = self.run_epoch("epoch %i (validation)" % epoch,
                                                                     self.valid_data, False)
                accs_str = " ".join(["%i:%.5f" % (id, acc) for (id, acc) in zip(self.params['task_ids'], valid_accs)])
                # errs_str = " ".join(["%i:%.5f" % (id, err) for (id, err) in zip(self.params['task_ids'], valid_errs)])
                print("\r\x1b[K Valid: loss: %.5f | acc: %s | instances/sec: %.2f" % (valid_loss,
                                                                                      accs_str,
                                                                                      valid_speed))

                print("sigmoid in graph", sigmoid_in_graph.shape)
                print("fianl_nodes_representations", final_nodes_representations.shape)

                epoch_time = time.time() - total_time_start
                log_entry = {
                    'epoch': epoch,
                    'time': epoch_time,
                    'train_results': (train_loss, train_accs.tolist(), train_speed),
                    'valid_results': (valid_loss, valid_accs.tolist(), valid_speed),
                }
                log_to_save.append(log_entry)
                with open(self.log_file, 'w') as f:
                    json.dump(log_to_save, f, indent=4)

                val_acc = np.sum(valid_accs)

                if val_acc > best_val_acc:
                    self.save_model(self.best_model_file)
                    print("  (Best epoch so far, cum. val. acc decreased to %.5f from %.5f. Saving to '%s')" % (
                                val_acc, best_val_acc, self.best_model_file))
                    best_val_acc = val_acc
                    best_val_acc_epoch = epoch

                elif epoch - best_val_acc_epoch >= self.params['patience']:
                    print("Stopping training after %i epochs without improvement on validation accuracy."
                          % self.params['patience'])

                    print("best_epoch %i" % best_val_acc_epoch)

                    if final_nodes_representations is not None:
                        np.save("nodes%i.npy" % epoch, final_nodes_representations)

                    np.save("graph.npy", sigmoid_in_graph)
                    break

    def save_model(self, path: str) -> None:
        weights_to_save = {}
        for variable in self.sess.graph.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
            assert variable.name not in weights_to_save
            weights_to_save[variable.name] = self.sess.run(variable)

        data_to_save = {
            "params": self.params,
            "weights": weights_to_save
        }

        with open(path, 'wb') as out_file:
            pickle.dump(data_to_save, out_file, pickle.HIGHEST_PROTOCOL)

    def initialize_model(self) -> None:
        init_op = tf.group(tf.global_variables_initializer(),
                           tf.local_variables_initializer())
        self.sess.run(init_op)

    def restore_model(self, path: str) -> None:
        print("Restoring weights from file %s." % path)
        with open(path, 'rb') as in_file:
            data_to_load = pickle.load(in_file)

        # Assert that we got the same model configuration
        assert len(self.params) == len(data_to_load['params'])
        for (par, par_value) in self.params.items():
            # Fine to have different task_ids:
            if par not in ['task_ids', 'num_epochs']:
                assert par_value == data_to_load['params'][par]

        variables_to_initialize = []
        with tf.name_scope("restore"):
            restore_ops = []
            used_vars = set()
            for variable in self.sess.graph.get_collection(tf.GraphKeys.GLOBAL_VARIABLES):
                used_vars.add(variable.name)
                if variable.name in data_to_load['weights']:
                    restore_ops.append(variable.assign(data_to_load['weights'][variable.name]))
                else:
                    print('Freshly initializing %s since no saved value was found.' % variable.name)
                    variables_to_initialize.append(variable)
            for var_name in data_to_load['weights']:
                if var_name not in used_vars:
                    print('Saved weights for %s not used by model.' % var_name)
            restore_ops.append(tf.variables_initializer(variables_to_initialize))
            self.sess.run(restore_ops)
