
import tensorflow as tf
import pickle
import numpy as np
import random
import datetime
from Match_LSTM import MatchLSTM
from Rnet import Rnet
from ESIM_model import ESIM
from seq_match_seq import SeqMatchSeq
import json
import os
os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"   # see issue #152
os.environ["CUDA_VISIBLE_DEVICES"]="1"
import DataUtils
from nltk.tokenize import word_tokenize
import json
import spacy
enNLP = spacy.load("en")
from tqdm import *
from sklearn.metrics import accuracy_score


tf.flags.DEFINE_string("dataset", "SemEval", "SemEval/QNLI")
tf.flags.DEFINE_string("mode", "pretrained", "pretrained/transfer")
# Training hyperparameter config
tf.flags.DEFINE_integer("batch_size", 64, "batch size")
tf.flags.DEFINE_integer("epochs", 160, "epochs")
tf.flags.DEFINE_float("learning_rate", 1e-3, "learning rate")
tf.flags.DEFINE_float("grad_clip", 5.0, "")
# LSTM config
tf.flags.DEFINE_integer("hidden_layer", 400, "")
tf.flags.DEFINE_integer("pad_question", 150, "")
tf.flags.DEFINE_integer("pad_sentence", 100, "")
tf.flags.DEFINE_float("dropout", 0.7, "")
tf.flags.DEFINE_string("Ddim", "2", "")
tf.flags.DEFINE_boolean("bidi", True, "")
tf.flags.DEFINE_string("rnnact", "tanh", "")
tf.flags.DEFINE_string("bidi_mode", "concatenate", "")
tf.flags.DEFINE_boolean("use_cudnn", True, "")
#self attention config
tf.flags.DEFINE_integer("attn_unit", 350, "")
tf.flags.DEFINE_integer("hop", 1, "")
# word vector config
tf.flags.DEFINE_string(
    "embedding_path", "glove.6B.300d.txt", "word embedding path")
tf.flags.DEFINE_boolean("use_char_embedding", True, "")
tf.flags.DEFINE_integer("char_embedding_dim", 50, "")
tf.flags.DEFINE_integer("char_pad", 15, "")
# Tensorflow config
tf.flags.DEFINE_integer("num_checkpoints", 2,
                        "Number of checkpoints to store (default: 5)")
tf.flags.DEFINE_string("out_dir", "runs/", "path to save checkpoint")
tf.flags.DEFINE_boolean("allow_soft_placement", True,
                        "Allow device soft device placement")
tf.flags.DEFINE_boolean("log_device_placement", False,
                        "Log placement of ops on devices")


FLAGS = tf.flags.FLAGS

def load_data_from_file(dsfile):
    q, q_l = [], [] # a set of questions
    sents, s_l = [], [] # a set of sentences
    labels = [] # a set of labels
    with open(dsfile) as f:          
        for l in f:
            label = l.strip().split("\t")[2]
            qtext = l.strip().split("\t")[0]
            stext = l.strip().split("\t")[1]
            q_tok = word_tokenize(qtext.lower())
            s_tok = word_tokenize(stext.lower())
            #q_tok = [w.string.strip() for w in enNLP(qtext.lower())]
            #s_tok = [w.string.strip() for w in enNLP(stext.lower())]
        
            q.append(q_tok+["<eos>"])
            q_l.append(min(len(q_tok), FLAGS.pad_question))
            sents.append(s_tok+["<eos>"])
            s_l.append(min(len(s_tok), FLAGS.pad_sentence))
            labels.append(int(label))
    return (q, sents,q_l, s_l,  labels)


def make_model_inputs(qi, si, qi_char, si_char, q_l, s_l, q, sents, y):
    inp = {'qi': qi, 'si': si, 'qi_char':qi_char, 'si_char': si_char,
           'q_l':q_l, 's_l':s_l, 'q':q, 'sents':sents, 'y':y} 
    
    return inp
 
def load_set(fname, vocab=None, char_vocab=None, iseval=False, pad_sentence=None):
    q, sents, q_l, s_l, y = load_data_from_file(fname)
    if not iseval:
        if vocab == None:
            vocab = DataUtils.Vocabulary(q + sents)
        else:
            vocab.update(q+sents)
        if char_vocab == None:
            char_vocab = DataUtils.CharVocabulary(q+sents)
        else:
            char_vocab.update(q+sents)
    pad_question = pad_sentence
    if pad_sentence is None:
        pad_sentence = FLAGS.pad_sentence
        pad_question = FLAGS.pad_question
    char_pad = FLAGS.char_pad
    
    qi = vocab.vectorize(q, pad=pad_question)  
    si = vocab.vectorize(sents, pad=pad_sentence)
    qi_char = char_vocab.vectorize(q, pad=char_pad, seq_pad=pad_question)
    si_char = char_vocab.vectorize(sents, pad=char_pad, seq_pad=pad_sentence)
    
    inp = make_model_inputs(qi, si, qi_char, si_char, q_l, s_l, q, sents, y)
    if iseval:
        return (inp, y)
    else:
        return (inp, y, vocab, char_vocab)        
    

def load_data(trainf, valf, testf):
    global vocab, char_vocab, inp_tr, inp_val, inp_test, y_train, y_val, y_test
    
    
    if FLAGS.mode == "pretrained":
        _,_, vocab, char_vocab = load_set("SemEval/test.txt", iseval=False)
        inp_tr, y_train, vocab, char_vocab = load_set(trainf, vocab, char_vocab, iseval=False)
        #inp_test, y_test, vocab, char_vocab = load_set("QNLI/train.txt", vocab, char_vocab, iseval=False)
    else:
        vocab = pickle.load(open("vocab.pkl", "rb"))
        char_vocab = pickle.load(open("char_vocab.pkl", "rb"))
        inp_tr, y_train = load_set(trainf, vocab, char_vocab, iseval=True)
    inp_val, y_val = load_set(valf, vocab, char_vocab, iseval=True)
    print("=" * 50)
    print("Sample in training data")
    print("q:",  inp_tr["q"][2])
    print("qi:", list(inp_tr["qi"][2]))
    print("sents:", inp_tr["sents"][2])
    print("si:", list(inp_tr["si"][2]))
    print("target:", inp_tr["y"][2])
    print("=" * 50)
    print("Train on {} pairs of sentece".format(len(inp_tr["q"])))
    print("Valid on {} pairs of sentece".format(len(inp_val["q"])))
    print("=" * 50)
    #inp_test, y_test = load_set(testf, vocab=vocab, iseval=True)


def SNLI_train_step(sess, model, data_batch):
    q_batch, s_batch, q_char_batch, s_char_batch, ql_batch, sl_batch, y_batch = data_batch
    y_batch_onehot = np.eye(3)[y_batch]
    feed_dict = {
        model.queries : q_batch,
        model.queries_char : q_char_batch,
        #model.queries_length : ql_batch,
        model.hypothesis : s_batch,
        model.hypothesis_char : s_char_batch,
        #model.hypothesis_length : sl_batch,
        model.dropout : 0.7,
        model.y_SNLI : y_batch
    }
    _, loss = sess.run([model.train_op_SNLI, model.loss_SNLI], feed_dict=feed_dict)
    return loss

def train_step(sess, model, data_batch):
    q_batch, s_batch, q_char_batch, s_char_batch, ql_batch, sl_batch, y_batch = data_batch
    feed_dict = {
        model.queries : q_batch,
        model.queries_char : q_char_batch,
        #model.queries_length : ql_batch,
        model.hypothesis : s_batch,
        model.hypothesis_char : s_char_batch,
        #model.hypothesis_length : sl_batch,
        model.dropout : FLAGS.dropout,
        model.y : y_batch,
    }
    _, loss = sess.run([model.train_op, model.loss], feed_dict=feed_dict)
    return loss


def SNLI_test_step(sess, model, test_data):
    q_test, s_test, q_char_batch, s_char_batch, ql_test, sl_test, y_test = test_data
    final_pred = []
    final_loss = []
    for i in range(0, len(y_test), FLAGS.batch_size):
        y_test_onehot = np.eye(3)[y_test[i:i+FLAGS.batch_size]]
        feed_dict = {
            model.queries : q_test[i:i+FLAGS.batch_size],
            model.queries_char : q_char_batch[i:i+FLAGS.batch_size],
            #model.queries_length : ql_test[i:i+FLAGS.batch_size],
            model.hypothesis : s_test[i:i+FLAGS.batch_size],
            model.hypothesis_char : s_char_batch[i:i+FLAGS.batch_size],
            #model.hypothesis_length : sl_test[i:i+FLAGS.batch_size],
            model.y_SNLI : y_test[i:i+FLAGS.batch_size],
            model.dropout : 1.0
        }
        loss, pred_label = sess.run([model.loss_SNLI, model.yp_SNLI], feed_dict=feed_dict)
        pred_label = list(pred_label.reshape((-1,1)))
        final_pred += pred_label
        final_loss += [loss] * len(pred_label)
    print("loss in valid set :{}".format(np.mean(final_loss)))
    acc = accuracy_score(y_true=y_test, y_pred=final_pred)
    print("acc %.6f" %(acc)) 
    return acc


def SemEval_test_step(sess, model, test_data, call_back, debug=False):
    q_test, s_test, q_char_batch, s_char_batch, ql_test, sl_test, y_test = test_data
    final_pred = []
    final_loss = []
    for i in range(0, len(y_test), FLAGS.batch_size):
        feed_dict = {
            model.queries : q_test[i:i+FLAGS.batch_size],
            model.queries_char: q_char_batch[i:i+FLAGS.batch_size],
            #model.queries_length : ql_test[i:i+FLAGS.batch_size],
            model.hypothesis : s_test[i:i+FLAGS.batch_size],
            model.hypothesis_char : s_char_batch[i:i+FLAGS.batch_size],
            #model.hypothesis_length : sl_test[i:i+FLAGS.batch_size],
            model.y : y_test[i:i+FLAGS.batch_size],
            model.dropout : 1.0
        }
        loss, pred_label = sess.run([model.loss, model.yp], feed_dict=feed_dict)
        pred_label = list(pred_label.reshape((-1,1)))
        final_pred += pred_label
        final_loss += [loss] * len(pred_label)
    print("loss in valid set :{}".format(np.mean(final_loss)))
    if debug:
        callback.on_debug(final_pred)        
    logs = call_back.on_epoch_end(final_pred)
    #print("In dev set: loss: {} MMR: {} MAP: {}".format(loss, logs['mrr'], logs['map']))
    return logs['map'], logs['mrr']


if __name__ == "__main__":
    trainf = os.path.join(FLAGS.dataset, 'train.txt')
    valf = os.path.join(FLAGS.dataset, 'test.txt')
    testf = os.path.join(FLAGS.dataset, 'dev.txt')
    best_map = 0
    mrr_on_best_map = 0
    best_epoch = 0
    print("Load data")
    load_data(trainf, valf, testf)
    
    pickle.dump(vocab, open("vocab.pkl","wb"))
    pickle.dump(char_vocab, open("char_vocab.pkl","wb"))
    json.dump(tf.app.flags.FLAGS.flag_values_dict(), open("config.json", "w"), indent=4)
    print("Load Glove")
    emb = DataUtils.GloVe(FLAGS.embedding_path)
    gpu_options = tf.GPUOptions(allow_growth=True)
    session_conf = tf.ConfigProto(
        gpu_options=gpu_options,
        allow_soft_placement=FLAGS.allow_soft_placement,
        log_device_placement=FLAGS.log_device_placement)
    sess = tf.Session(config=session_conf) 
    model = MatchLSTM(FLAGS, vocab, char_vocab, emb)
    checkpoint_dir = os.path.abspath(os.path.join(FLAGS.out_dir, "checkpoints"))
    if not os.path.exists(checkpoint_dir):
        os.makedirs(checkpoint_dir)
    saver = tf.train.Saver(tf.global_variables(), max_to_keep=FLAGS.num_checkpoints)
    callback = DataUtils.AnsSelCB(inp_val['q'], inp_val['sents'], y_val, inp_val)
    test_data = [ inp_val['qi'],
                  inp_val['si'],
                  inp_val['qi_char'],
                  inp_val['si_char'],
                  inp_val['q_l'],
                  inp_val['s_l'],
                  y_val
    ]
    if FLAGS.mode == "pretrained":
        sess.run(tf.global_variables_initializer())
    elif FLAGS.mode == "debug":
         last_checkpoint = tf.train.latest_checkpoint(checkpoint_dir)
         saver.restore(sess,last_checkpoint)
         print("loaded model from checkpoint {}".format(last_checkpoint))
         SemEval_test_step(sess, model, test_data, callback, debug=True)
         exit()
    else:
        last_checkpoint = tf.train.latest_checkpoint(checkpoint_dir)
        saver.restore(sess,last_checkpoint)
        print("loaded model from checkpoint {}".format(last_checkpoint))
        best_map, mrr_on_best_map = SemEval_test_step(sess, model, test_data, callback, debug=True)
    for e in range(FLAGS.epochs):
        train_loss = []
        train_SNLI_loss = []
        # t_SNLI = tqdm(range(0, len(y_test), FLAGS.batch_size), desc='train SNLI loss: %.6f' % (0.0), ncols=100)
        # for i in t_SNLI:
        #     data_batch = [ inp_test['qi'][i:i+FLAGS.batch_size],
        #                    inp_test['si'][i:i+FLAGS.batch_size],
        #                    inp_test['qi_char'][i:i+FLAGS.batch_size],
        #                    inp_test['si_char'][i:i+FLAGS.batch_size],
        #                    inp_test['q_l'][i:i+FLAGS.batch_size],
        #                    inp_test['s_l'][i:i+FLAGS.batch_size],
        #                    y_test[i:i+FLAGS.batch_size]
        #     ]
        #     loss = SNLI_train_step(sess, model, data_batch)
        #     train_SNLI_loss.append(loss)
        #     t_SNLI.set_description("epoch %d: train SNLI loss %.6f" % (e, np.mean(train_SNLI_loss)))
        #     t_SNLI.refresh()
        t = tqdm(range(0, len(y_train), FLAGS.batch_size), desc='train loss: %.6f' % (0.0), ncols=100)
        
        for i in t:
            
            data_batch = [ inp_tr['qi'][i:i+FLAGS.batch_size],
                           inp_tr['si'][i:i+FLAGS.batch_size],
                           inp_tr['qi_char'][i:i+FLAGS.batch_size],
                           inp_tr['si_char'][i:i+FLAGS.batch_size],
                           inp_tr['q_l'][i:i+FLAGS.batch_size],
                           inp_tr['s_l'][i:i+FLAGS.batch_size],
                           y_train[i:i+FLAGS.batch_size]
            ]
            if FLAGS.dataset == "SemEval" or FLAGS.dataset == "TrecQA":
                loss = train_step(sess, model, data_batch)
                #loss_SNLI = SNLI_train_step(sess, model, data_batch)
            else:
                loss = SNLI_train_step(sess, model, data_batch)
            train_loss.append(loss)
            t.set_description("epoch %d: train loss %.6f" % (e, np.mean(train_loss)))
            t.refresh()

            
        if FLAGS.dataset == "SemEval" or FLAGS.dataset == "TrecQA":
            curr_map, curr_mrr = SemEval_test_step(sess, model, test_data, callback)
        elif FLAGS.dataset == "QNLI":
            #curr_map, curr_mrr = SemEval_test_step(sess, model, test_data, callback)
            curr_map = SNLI_test_step(sess, model, test_data)
            print("Best MAP:%.6f on epoch %d with MRR:%.6f" %(best_map, best_epoch, mrr_on_best_map))
        if curr_map > best_map:
            best_map = curr_map
            best_epoch = e
            if FLAGS.dataset == "SemEval" or FLAGS.dataset == "TrecQA":
                mrr_on_best_map = curr_mrr
            save_path = saver.save(sess, os.path.join(checkpoint_dir, "checkpoint"), e)
