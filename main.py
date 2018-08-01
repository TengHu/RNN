from __future__ import unicode_literals, print_function, division
from io import open
import os
import glob
import torch
import torch.nn.functional as F
import torchvision.models as models
import models
import random
import numpy as np
from torch.autograd import Variable
import torch.optim as optim
import torch.nn as nn
import string
import time
import math
import torch.utils.data as data_utils
import shutil
import data
import pdb
import argparse
from utils import one_hot
from utils import count_trainable_params

import data

import multiprocessing as mp

torch.manual_seed(1)
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

torch.backends.cudnn.enabled = True
torch.backends.cudnn.benchmark = True

###############################################################################
parser = argparse.ArgumentParser(description='Character Level Language Model')

parser.add_argument(
    '--num_workers',
    type=int,
    default=2,
    help='number of worker to load the data')

parser.add_argument(
    '--train',
    type=str,
    default='data/ptb.train.txt',
    help='location of the data corpus')

parser.add_argument(
    '--test',
    type=str,
    default='data/ptb.test.txt',
    help='location of the data corpus')

parser.add_argument(
    '--valid',
    type=str,
    default='data/ptb.valid.txt',
    help='location of the data corpus')

parser.add_argument(
    '--import_model',
    type=str,
    default='NONE',
    help='import model if specified otherwise train from random initialization'
)

parser.add_argument(
    '--model', type=str, default='DLSTM3', help='models: DLSTM3')

parser.add_argument(
    '--position_codes',
    type=str,
    default='',
    help='number of position features')

parser.add_argument(
    '--position_feature_size',
    type=int,
    default=100,
    help='position feature size')

parser.add_argument(
    '--hidden_size', type=int, default=128, help='# of hidden units')

parser.add_argument(
    '--batch_size', type=int, default=50, help='# of hidden units')

parser.add_argument('--epochs', type=int, default=3, help='# of epochs')

parser.add_argument(
    '--lr', type=float, default=0.001, help='initial learning rate')

parser.add_argument('--clip', type=float, default=1, help='gradient clipp')

parser.add_argument(
    '--bptt', type=int, default=50, help='backprop sequence length')

parser.add_argument(
    '--print_every', type=int, default=50, help='print every # iterations')

parser.add_argument(
    '--save_every',
    type=int,
    default=2000,
    help='save model every # iterations')

parser.add_argument(
    '--plot_every',
    type=int,
    default=50,
    help='plot the loss every # iterations')

parser.add_argument(
    '--sample_every',
    type=int,
    default=300,
    help='print the sampled text every # iterations')

parser.add_argument(
    '--output_file',
    type=str,
    default="output",
    help='sample characters and save to output file')

parser.add_argument(
    '--max_sample_length',
    type=int,
    default=500,
    help='max sampled characters')

args = parser.parse_args()

###############################################################################
# Data Preprocessing Helper Functions
###############################################################################


def save_checkpoint(state, filename='checkpoint.pth'):
    '''
    One dimensional array
    '''
    torch.save(state, filename)
    print("{} saved ! \n".format(filename))



def get_batch(data, idx):
    '''
    Input: (number_of_chars, ids)
    Output: 
    (batch_size, sequence, ids)
    (batch_size)
    '''
    inputs = data[:, idx:(idx + args.bptt), :]
    targets = data[:, (idx + args.bptt)]

    ## For target, we only need the first char element, discard position codes
    return inputs, targets[:, 0].long()


def sequentialize(data, mode="default"):
    '''
    Input: (number of chars, ids)
    Output:
    (batch, sequence, ids)
    (batch)
    batch_size = 1
    '''

    if mode=="default":
        ids_size = data.shape[1]
        nbatch = data.shape[0] // args.bptt
        inputs = data[0:(nbatch*args.bptt), :].view(-1, args.bptt, ids_size)
        targets = inputs[1:, 0, 0]

        # for each sequence, targets is the first character of next sequence,
        # only want the character id as target
        inputs = inputs[:(targets.shape[0]),:,:] # drop last line to make data the same size as targets
    elif mode=="line_by_line":
        # check args.bptt compatible ?
        # batch size == 1 ? for different size in the file ? 
        '''
        Sample data:
        length 4, last one is target
        11011 \n
        10010 \n
        ...
        '''
        ids_size = data.shape[1]
        newline_idx = (data==corpus.vocabulary.char2idx['\n']).nonzero()[:,0]

        max_length = 1
        for i, v in enumerate(newline_idx):
            if i == 0:
                max_length = max(max_length, newline_idx[i]-1)
            else:
                max_length = max(max_length, newline_idx[i]-newline_idx[i-1]-2)
        max_length += 1 # leave the final character always be \n
        inputs = torch.ones(len(newline_idx), max_length, ids_size) * corpus.vocabulary.char2idx['\n']
        targets = torch.Tensor(len(newline_idx))
     
        for i, v in enumerate(newline_idx):
            if i == 0:
                inputs[0, :(newline_idx[i]-1)] = data[0: newline_idx[i]-1]
            else:
                inputs[i, :(newline_idx[i]-newline_idx[i-1]-2)] = data[newline_idx[i-1]+1: newline_idx[i]-1]
            targets[i] = data[newline_idx[i]-1][0]

    pdb.set_trace()
    return inputs, targets
    
def tensor2idx(tensor):
    '''
    Input: (#batch, feature)
    Output: (#batch)
    '''
    batch_size = tensor.shape[0]
    idx = np.zeros((batch_size), dtype=np.int64)

    for i in range(0, batch_size):
        value, indice = tensor[i].max(0)  # dimension 0
        idx[i] = indice
    return torch.LongTensor(idx)


def preprocess(data, mode="default"):
    '''
    Input (number of characters, ids)
    Output Dataset((batch size, sequence, features), (batch size))
    batch size = 1
    '''

    inputs, targets = sequentialize(data, mode=mode)
    
    inputs = one_hot(inputs, feature_size)

    return torch.utils.data.TensorDataset(
        inputs, targets)  # CUDA


###############################################################################
# Training Helper Functions
###############################################################################

def get_loss(outputs, targets):
    loss = 0
    loss += criterion(outputs[:, -1, :], targets.long())
    return loss

def sample(text, save_to_file=False, max_sample_length=300, temperature=100.0):
    '''
    Havent figured out how to do sampling with positional encoding
    '''
    try:
        assert len(text) >= args.bptt
    except AssertionError:
        print("\nSampling must start with text has more than {} characters \n".
              format(args.bptt))
   
    output_text = text
    text = text[-args.bptt:]  # drop last incomplete sequence

    ids = torch.LongTensor(len(text), 1)
    token = 0
    for c in text:
        ## character id
        ids[token][0] = corpus.vocabulary.char2idx[c]
        token += 1

    inputs = ids.unsqueeze(0) # create batch dimension, batch size = 1 (1, bptt, 1)
    hiddens = model.initHidden(batch_size=1)

    for i in range(0, max_sample_length):
        one_hot_input = one_hot(inputs, feature_size).to(device) # (1, bptt, feature)
        outputs, hiddens = model(one_hot_input, hiddens)
        # TODO (niel.hu) temporarily use vocabulary size as feature size
        
        # sample character
        char_id = torch.multinomial(
            outputs[0][-1].exp() / temperature, num_samples=1).item()
        output_text += corpus.vocabulary.idx2char[char_id]

        # drop first character, append text
        text = text[1:] + corpus.vocabulary.idx2char[char_id]

        # rolling input text
        inputs[0][0:(args.bptt - 1)] = inputs[0][1:]
        inputs[0][args.bptt - 1][0] = char_id
        del outputs

    if save_to_file:
        with open(args.output_file, 'w') as f:
            f.write(output_text)
            print("Finished sampling and saved it to {}".format(
                args.output_file))
    else:
        print('#' * 90)
        print("Sampling Text, Starts from first {} warm up characters : \n".format(args.bptt) + output_text + "\n")
        print('#' * 90)


def detach(layers):
    '''
    Remove variables' parent node after each sequence, 
    basically no where to propagate gradient 
    '''
    if (type(layers) is list) or (type(layers) is tuple):
        for l in layers:
            detach(l)
    else:
        layers.detach_()
        #layers = layers.detach()


def train(dataset):
    losses = []
    total_loss = 0
    hiddens = model.initHidden(batch_size=args.batch_size)
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        drop_last=True,
        pin_memory=True)
    for batch_idx, data in enumerate(dataloader, 0):
        inputs, targets = data

        inputs = inputs.to(device)
        targets = targets.to(device)
        
        detach(hiddens)
        optimizer.zero_grad()

        outputs, hiddens = model(inputs, hiddens)
        loss = get_loss(outputs, targets)
        loss.backward(retain_graph=True)

        torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
        optimizer.step()
        total_loss += loss.item()
        if batch_idx % args.print_every == 0 and batch_idx > 0:
            print(
                "Epoch : {} / {}, Iteration {} / {}, Loss every {} iteration :  {}, Takes {} Seconds".
                format(epoch, args.epochs, batch_idx,
                       int(len(dataset) / (args.batch_size)), args.print_every,
                       loss.item(),
                       time.time() - start))

        if batch_idx % args.plot_every == 0 and batch_idx > 0:
            losses.append(total_loss / args.plot_every)
            total_loss = 0

        if batch_idx % args.sample_every == 0 and batch_idx > 0:
            pass#sample(warm_up_text)

        if batch_idx % args.save_every == 0 and batch_idx > 0:
            save_checkpoint({
                'epoch': epoch,
                'iter': batch_idx,
                'losses': losses,
                'state_dict': model.state_dict(),
                'optimizer': optimizer.state_dict(),
            }, "checkpoint_{}_epoch_{}_iteration_{}.{}.pth".format(
                int(time.time()), epoch, batch_idx, model_type))

        del loss, outputs    
    return losses


def evaluate(dataset, dynamic_evaluation=False):
    hiddens = model.initHidden(batch_size=args.batch_size)
    total = 0.0
    correct = 0.0
    voc_length = len(corpus.vocabulary)
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        drop_last=True,
        pin_memory=True)

    bpc = []
    for batch_idx, data in enumerate(dataloader, 0):
        inputs, targets = data
        
        optimizer.zero_grad()
        
        inputs = inputs.to(device)
        targets = targets.to(device)
        
        detach(hiddens)

        outputs, hiddens = model(inputs, hiddens)
        loss = get_loss(outputs, targets)
        bpc.append(loss)
        
        loss.backward(retain_graph=True)
        if dynamic_evaluation == True:
            optimizer.step()
        
        total += outputs.shape[0]
        ## only need feature vocabulary length
        correct += (tensor2idx(outputs[:, -1, :voc_length]) == targets.long().cpu()).sum()

    if total == 0:
        print("Validation Set too small, reduce batch size")
    else:
        ## Debug Accuracy calculation
        print(
            "Evaluation: Bits-per-character: {}, \nPerplexity: {}\nAccuracy: {} % \n".
            format(sum(bpc) / len(bpc), "N/A",
                   correct.numpy() / total * 100))
    try:
        del loss, outputs
    except Exception as e:
        print ("error in validation: {}".format(e))

if __name__ == "__main__":
    #mp.set_start_method(
    #    'forkserver')  # for sharing CUDA tensors between processes

    ###############################################################################
    # Load Data And PreProcessing
    ###############################################################################
    print(
        "Loading Data, Be aware of old cache may not be compatible with loading data!!\n"
    )
    
    '''
    inputs to model: (batch, sequence, feature)
    targets: (batch)
    '''

    mode = "line_by_line"
    #mode = "default"

    corpus = data.get_corpus(
        path=args.train, special_tokens=args.position_codes)
    feature_size = len(corpus.vocabulary)

    
    train_data = corpus.data
    train_dataset = preprocess(train_data, mode=mode)
    
    '''
    Use train corpus dictionary to encode valid and test set.
    '''
    valid_data = data.get_corpus(corpus=corpus, path=args.valid).data
    valid_dataset = preprocess(valid_data, mode=mode)

    test_data = data.get_corpus(corpus=corpus, path=args.test).data
    test_dataset = preprocess(test_data, mode=mode)


    
    ###############################################################################
    # Build Model
    ###############################################################################

    hidden_size = args.hidden_size
    model_type = args.model

    ### Determine model type
    if args.import_model != 'NONE':
        print("=> loading checkpoint ")
        if torch.cuda.is_available() is False:
            checkpoint = torch.load(
                args.import_model, map_location=lambda storage, loc: storage)
        else:
            checkpoint = torch.load(args.import_model)

        model_type = args.import_model.split('.')[1]

        if model_type == 'DLSTM3':
            model = models.DLSTM3(feature_size, hidden_size)
            model.load_state_dict(checkpoint['state_dict'])
        else:
            raise ValueError("Model type not recognized")
    else:
        if model_type == 'DLSTM3':
            model = models.DLSTM3(feature_size, hidden_size)
        elif model_type == 'SingleLSTM':
            model = models.SingleLSTM(feature_size, hidden_size)
        else:
            raise ValueError("Model type not recognized")

    model = model.to(device)

    ### Size of model
    print ("This model has {} trainable parameters".format(count_trainable_params(model)))
    ###############################################################################
    # Training code
    ###############################################################################


    ### Optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    try:
        optimizer.load_state_dict(checkpoint['optimizer'])
    except NameError:
        print("Optimizer initializing")


    ### Loss function 
    criterion = nn.NLLLoss().to(device)
    warm_up_text = open(args.train, encoding='utf-8').read()[0:args.bptt]

    '''
    Training Loop
    Can interrupt with Ctrl + C
    '''
    start = time.time()
    all_losses = []

    try:
        print("Start Training\n")
        for epoch in range(1, args.epochs + 1):
            loss = train(train_dataset)
            all_losses += loss
            evaluate(valid_dataset)
    except KeyboardInterrupt:
        print('#' * 90)
        print('Exiting from training early')

    print("Finished Training!\n")

    print("Testing")

    evaluate(test_dataset)    
    #sample(warm_up_text, save_to_file=False, max_sample_length=args.max_sample_length)

    '''
    publish for analytics and visualization
    '''
    with open("losses", 'w') as f:
        f.write(str(all_losses))

    model_name = "{}.{}.pth".format(model_type, int(time.time()))
    save_checkpoint({'state_dict': model.state_dict()}, model_name)
    print("Model {} Saved".format(model_name))

    print('#' * 90)
    print("Training finished ! Takes {} seconds ".format(int(time.time() - start)))
