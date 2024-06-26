'''
Multitask BERT class, starter training code, evaluation, and test code.

Of note are:
* class MultitaskBERT: Your implementation of multitask BERT.
* function train_multitask: Training procedure for MultitaskBERT. Starter code
    copies training procedure from `classifier.py` (single-task SST).
* function test_multitask: Test procedure for MultitaskBERT. This function generates
    the required files for submission.

Running `python multitask_classifier.py` trains and tests your MultitaskBERT and
writes all required submission files.
'''

import random, numpy as np, argparse
from types import SimpleNamespace

import torch
from torch import nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from bert import BertModel
from optimizer import AdamW
from tqdm import tqdm

from pcgrad import PCGrad

from datasets import (
    SentenceClassificationDataset,
    SentenceClassificationTestDataset,
    SentencePairDataset,
    SentencePairTestDataset,
    load_multitask_data
)

from evaluation import model_eval_sst, model_eval_multitask, model_eval_test_multitask, model_eval_para, model_eval_sts

import os

TQDM_DISABLE=False


# Fix the random seed.
def seed_everything(seed=11711):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


BERT_HIDDEN_SIZE = 768
N_SENTIMENT_CLASSES = 5


class MultitaskBERT(nn.Module):
    '''
    This module should use BERT for 3 tasks:

    - Sentiment classification (predict_sentiment)
    - Paraphrase detection (predict_paraphrase)
    - Semantic Textual Similarity (predict_similarity)
    '''
    def __init__(self, config):
        super(MultitaskBERT, self).__init__()
        self.bert = BertModel.from_pretrained('bert-base-uncased')
        # last-linear-layer mode does not require updating BERT paramters.
        assert config.fine_tune_mode in ["last-linear-layer", "full-model"]
        for param in self.bert.parameters():
            if config.fine_tune_mode == 'last-linear-layer':
                param.requires_grad = False
            elif config.fine_tune_mode == 'full-model':
                param.requires_grad = True
        # You will want to add layers here to perform the downstream tasks.
        ### TODO
        self.last_dropout = torch.nn.Dropout(config.hidden_dropout_prob)
        self.last_sentiment = torch.nn.Linear(config.hidden_size, len(config.num_labels))
        self.last_para = torch.nn.Bilinear(config.hidden_size, config.hidden_size, 1)
        self.last_similar = torch.nn.Bilinear(config.hidden_size, config.hidden_size, 1)


    def forward(self, input_ids, attention_mask):
        'Takes a batch of sentences and produces embeddings for them.'
        # The final BERT embedding is the hidden state of [CLS] token (the first token)
        # Here, you can start by just returning the embeddings straight from BERT.
        # When thinking of improvements, you can later try modifying this
        # (e.g., by adding other layers).
        ### TODO
        bert_out = self.bert.forward(input_ids, attention_mask)
        return bert_out


    def predict_sentiment(self, input_ids, attention_mask):
        '''Given a batch of sentences, outputs logits for classifying sentiment.
        There are 5 sentiment classes:
        (0 - negative, 1- somewhat negative, 2- neutral, 3- somewhat positive, 4- positive)
        Thus, your output should contain 5 logits for each sentence.
        '''
        ### TODO
        bert_out = self.forward(input_ids, attention_mask)
        sent = self.last_sentiment(self.last_dropout(bert_out['pooler_output']))
        return sent

    def predict_paraphrase(self,
                           input_ids_1, attention_mask_1,
                           input_ids_2, attention_mask_2):
        '''Given a batch of pairs of sentences, outputs a single logit for predicting whether they are paraphrases.
        Note that your output should be unnormalized (a logit); it will be passed to the sigmoid function
        during evaluation.
        '''
        ### TODO
        bert_out_1 = self.forward(input_ids_1, attention_mask_1)
        bert_out_2 = self.forward(input_ids_2, attention_mask_2)
        pred = self.last_para(self.last_dropout(bert_out_1['pooler_output']), self.last_dropout(bert_out_2['pooler_output']))
        return pred
    

    def predict_similarity(self,
                           input_ids_1, attention_mask_1,
                           input_ids_2, attention_mask_2):
        '''Given a batch of pairs of sentences, outputs a single logit corresponding to how similar they are.
        Note that your output should be unnormalized (a logit).
        '''
        ### TODO
        bert_out_1 = self.forward(input_ids_1, attention_mask_1)
        bert_out_2 = self.forward(input_ids_2, attention_mask_2)
        pred = self.last_similar(self.last_dropout(bert_out_1['pooler_output']), self.last_dropout(bert_out_2['pooler_output']))
        return pred

def sst_loss(logits, b_labels, args):
    return F.cross_entropy(logits, b_labels.view(-1), reduction='sum') / args.sst_batch_size

def para_loss(logits, b_labels, args):
    return F.binary_cross_entropy(torch.sigmoid(torch.squeeze(logits)).float(), b_labels.view(-1).float(), reduction='sum') / args.para_batch_size

def sts_loss(logits, b_labels, args):
    # Loss for STS task         
    return torch.sum(1 - F.cosine_similarity(logits, b_labels.view(-1))) / args.sts_batch_size

def save_model(model, optimizer, args, config, filepath):
    save_info = {
        'model': model.state_dict(),
        'optim': optimizer.state_dict(),
        'args': args,
        'model_config': config,
        'system_rng': random.getstate(),
        'numpy_rng': np.random.get_state(),
        'torch_rng': torch.random.get_rng_state(),
    }

    torch.save(save_info, filepath)
    print(f"save the model to {filepath}")

def train_PCGrad(args):
    '''Train MultitaskBERT.
    Train using all loss functions for all tasks
    '''
    device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
    # Create the data and its corresponding datasets and dataloader.
    sst_train_data, num_labels,para_train_data, sts_train_data = load_multitask_data(args.sst_train,args.para_train,args.sts_train, split ='train')
    sst_dev_data, num_labels,para_dev_data, sts_dev_data = load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev, split ='train')

    sst_train_data = SentenceClassificationDataset(sst_train_data, args)
    sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

    sst_train_dataloader = DataLoader(sst_train_data, shuffle=True, batch_size=args.sst_batch_size,
                                      collate_fn=sst_train_data.collate_fn)
    sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.sst_batch_size,
                                    collate_fn=sst_dev_data.collate_fn)
    
    para_train_data = SentencePairDataset(para_train_data, args)
    para_dev_data = SentencePairDataset(para_dev_data, args)

    para_train_dataloader = DataLoader(para_train_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_train_data.collate_fn)
    
    para_dev_dataloader = DataLoader(para_dev_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_dev_data.collate_fn)
    
    sts_train_data = SentencePairDataset(sts_train_data, args)
    sts_dev_data = SentencePairDataset(sts_dev_data, args)

    sts_train_dataloader = DataLoader(sts_train_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_train_data.collate_fn)
    
    sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_dev_data.collate_fn)

    # Init model.
    config = {'hidden_dropout_prob': args.hidden_dropout_prob,
              'num_labels': num_labels,
              'hidden_size': 768,
              'data_dir': '.',
              'fine_tune_mode': args.fine_tune_mode}

    config = SimpleNamespace(**config)

    model = MultitaskBERT(config)
    model = model.to(device)
    
    if args.model_path is not None:
        print('Loading previously trained model')
        model.load_state_dict(torch.load(args.model_path))
        
    
    lr = args.lr
    optimizer = PCGrad(torch.optim.Adam(model.parameters(), lr=lr))
    best_dev_acc = 0
    
    # Run for the specified number of epochs.
    print('Training with PCGrad')
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        for i in tqdm(range(1000)):
            batch_sst = next(iter(sst_train_dataloader))
            batch_para = next(iter(para_train_dataloader))
            batch_sts = next(iter(sts_train_dataloader))

            b_sst_ids, b_sst_mask, b_sst_labels = (batch_sst['token_ids'],
                                       batch_sst['attention_mask'], batch_sst['labels'])

            b_sst_ids = b_sst_ids.to(device)
            b_sst_mask = b_sst_mask.to(device)
            b_sst_labels = b_sst_labels.to(device)

            b_para_ids_1, b_para_mask_1, b_para_ids_2, b_para_mask_2, b_para_labels = (batch_para['token_ids_1'],
                                       batch_para['attention_mask_1'], batch_para['token_ids_2'], 
                                       batch_para['attention_mask_2'], batch_para['labels'])
            
            b_para_ids_1 = b_para_ids_1.to(device)
            b_para_mask_1 = b_para_mask_1.to(device)
            b_para_ids_2 = b_para_ids_2.to(device)
            b_para_mask_2 = b_para_mask_2.to(device)
            b_para_labels = b_para_labels.to(device)

            b_sts_ids_1, b_sts_mask_1, b_sts_ids_2, b_sts_mask_2, b_sts_labels = (batch_sts['token_ids_1'],
                                       batch_sts['attention_mask_1'], batch_sts['token_ids_2'], 
                                       batch_sts['attention_mask_2'], batch_sts['labels'])
            b_sts_ids_1 = b_sts_ids_1.to(device)
            b_sts_mask_1 = b_sts_mask_1.to(device)
            b_sts_ids_2 = b_sts_ids_2.to(device)
            b_sts_mask_2 = b_sts_mask_2.to(device)
            b_sts_labels = b_sts_labels.to(device)


            optimizer.zero_grad()

            logits_sst = model.predict_sentiment(b_sst_ids, b_sst_mask)
            # loss_sst = F.cross_entropy(logits_sst, b_sst_labels.view(-1), reduction='sum') / args.sst_batch_size
            l1 = sst_loss(logits_sst, b_sst_labels, args)

            logits_para = model.predict_paraphrase(b_para_ids_1, b_para_mask_1, b_para_ids_2, b_para_mask_2)
            l2 = para_loss(logits_para, b_para_labels, args)

            logits_sts = model.predict_similarity(b_sts_ids_1, b_sts_mask_1, b_sts_ids_2, b_sts_mask_2)            
            l3 = sts_loss(logits_sts, b_sts_labels, args)
            # Sum loss
            loss_sum = l1 + l2 + l3
            losses = [l1, l2, l3]

            optimizer.pc_backward(losses)
            optimizer.step()

            train_loss += loss_sum.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        train_acc, train_f1, *_ = model_eval_sst(sst_train_dataloader, model, device)
        dev_acc_sst, dev_f1, *_ = model_eval_sst(sst_dev_dataloader, model, device)
        # train_acc_para, train_f1, *_ = model_eval_para(para_train_dataloader, model, device)
        dev_acc_para, dev_f1, *_ = model_eval_para(para_dev_dataloader, model, device)
        dev_acc_sts, def_f1, *_ = model_eval_sts(sts_dev_dataloader, model, device)

        dev_acc = (dev_acc_sst + dev_acc_para + dev_acc_sts)/3

        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer.optimizer, args, config, args.filepath)

        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")

def train_simultaneous(args):
    '''Train MultitaskBERT.
    Train using all loss functions for all tasks
    '''
    device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
    # Create the data and its corresponding datasets and dataloader.
    sst_train_data, num_labels,para_train_data, sts_train_data = load_multitask_data(args.sst_train,args.para_train,args.sts_train, split ='train')
    sst_dev_data, num_labels,para_dev_data, sts_dev_data = load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev, split ='train')

    sst_train_data = SentenceClassificationDataset(sst_train_data, args)
    sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

    sst_train_dataloader = DataLoader(sst_train_data, shuffle=True, batch_size=args.sst_batch_size,
                                      collate_fn=sst_train_data.collate_fn)
    sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.sst_batch_size,
                                    collate_fn=sst_dev_data.collate_fn)
    
    para_train_data = SentencePairDataset(para_train_data, args)
    para_dev_data = SentencePairDataset(para_dev_data, args)

    para_train_dataloader = DataLoader(para_train_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_train_data.collate_fn)
    
    para_dev_dataloader = DataLoader(para_dev_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_dev_data.collate_fn)
    
    sts_train_data = SentencePairDataset(sts_train_data, args)
    sts_dev_data = SentencePairDataset(sts_dev_data, args)

    sts_train_dataloader = DataLoader(sts_train_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_train_data.collate_fn)
    
    sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_dev_data.collate_fn)

    # Init model.
    config = {'hidden_dropout_prob': args.hidden_dropout_prob,
              'num_labels': num_labels,
              'hidden_size': 768,
              'data_dir': '.',
              'fine_tune_mode': args.fine_tune_mode}

    config = SimpleNamespace(**config)

    model = MultitaskBERT(config)
    model = model.to(device)
    
    if args.model_path is not None:
        print('Loading previously trained model')
        model.load_state_dict(torch.load(args.model_path))
        
    
    lr = args.lr
    optimizer = AdamW(model.parameters(), lr=lr)
    best_dev_acc = 0
    
    # Run for the specified number of epochs.
    print('Training alltogether')
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        for i in tqdm(range(1000)):
            batch_sst = next(iter(sst_train_dataloader))
            batch_para = next(iter(para_train_dataloader))
            batch_sts = next(iter(sts_train_dataloader))

            b_sst_ids, b_sst_mask, b_sst_labels = (batch_sst['token_ids'],
                                       batch_sst['attention_mask'], batch_sst['labels'])

            b_sst_ids = b_sst_ids.to(device)
            b_sst_mask = b_sst_mask.to(device)
            b_sst_labels = b_sst_labels.to(device)

            b_para_ids_1, b_para_mask_1, b_para_ids_2, b_para_mask_2, b_para_labels = (batch_para['token_ids_1'],
                                       batch_para['attention_mask_1'], batch_para['token_ids_2'], 
                                       batch_para['attention_mask_2'], batch_para['labels'])
            
            b_para_ids_1 = b_para_ids_1.to(device)
            b_para_mask_1 = b_para_mask_1.to(device)
            b_para_ids_2 = b_para_ids_2.to(device)
            b_para_mask_2 = b_para_mask_2.to(device)
            b_para_labels = b_para_labels.to(device)

            b_sts_ids_1, b_sts_mask_1, b_sts_ids_2, b_sts_mask_2, b_sts_labels = (batch_sts['token_ids_1'],
                                       batch_sts['attention_mask_1'], batch_sts['token_ids_2'], 
                                       batch_sts['attention_mask_2'], batch_sts['labels'])
            b_sts_ids_1 = b_sts_ids_1.to(device)
            b_sts_mask_1 = b_sts_mask_1.to(device)
            b_sts_ids_2 = b_sts_ids_2.to(device)
            b_sts_mask_2 = b_sts_mask_2.to(device)
            b_sts_labels = b_sts_labels.to(device)


            optimizer.zero_grad()

            logits_sst = model.predict_sentiment(b_sst_ids, b_sst_mask)
            # loss_sst = F.cross_entropy(logits_sst, b_sst_labels.view(-1), reduction='sum') / args.sst_batch_size
            l1 = sst_loss(logits_sst, b_sst_labels, args)

            logits_para = model.predict_paraphrase(b_para_ids_1, b_para_mask_1, b_para_ids_2, b_para_mask_2)
            l2 = para_loss(logits_para, b_para_labels, args)

            logits_sts = model.predict_similarity(b_sts_ids_1, b_sts_mask_1, b_sts_ids_2, b_sts_mask_2)            
            l3 = sts_loss(logits_sts, b_sts_labels, args)
            # Sum loss
            loss = l1 + l2 + l3

            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        train_acc, train_f1, *_ = model_eval_sst(sst_train_dataloader, model, device)
        dev_acc_sst, dev_f1, *_ = model_eval_sst(sst_dev_dataloader, model, device)
        # train_acc_para, train_f1, *_ = model_eval_para(para_train_dataloader, model, device)
        dev_acc_para, dev_f1, *_ = model_eval_para(para_dev_dataloader, model, device)
        dev_acc_sts, def_f1, *_ = model_eval_sts(sts_dev_dataloader, model, device)

        dev_acc = (dev_acc_sst + dev_acc_para + dev_acc_sts)/3

        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer, args, config, args.filepath)

        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")


def train_multitask(args):
    '''Train MultitaskBERT.

    Currently only trains on SST dataset. The way you incorporate training examples
    from other datasets into the training procedure is up to you. To begin, take a
    look at test_multitask below to see how you can use the custom torch `Dataset`s
    in datasets.py to load in examples from the Quora and SemEval datasets.
    '''
    device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
    # Create the data and its corresponding datasets and dataloader.
    sst_train_data, num_labels,para_train_data, sts_train_data = load_multitask_data(args.sst_train,args.para_train,args.sts_train, split ='train')
    sst_dev_data, num_labels,para_dev_data, sts_dev_data = load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev, split ='train')

    sst_train_data = SentenceClassificationDataset(sst_train_data, args)
    sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

    sst_train_dataloader = DataLoader(sst_train_data, shuffle=True, batch_size=args.sst_batch_size,
                                      collate_fn=sst_train_data.collate_fn)
    sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.sst_batch_size,
                                    collate_fn=sst_dev_data.collate_fn)
    
    para_train_data = SentencePairDataset(para_train_data, args)
    para_dev_data = SentencePairDataset(para_dev_data, args)

    para_train_dataloader = DataLoader(para_train_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_train_data.collate_fn)
    
    para_dev_dataloader = DataLoader(para_dev_data, shuffle=True, batch_size=args.para_batch_size,
                                       collate_fn=para_dev_data.collate_fn)
    
    sts_train_data = SentencePairDataset(sts_train_data, args)
    sts_dev_data = SentencePairDataset(sts_dev_data, args)

    sts_train_dataloader = DataLoader(sts_train_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_train_data.collate_fn)
    
    sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=True, batch_size=args.sts_batch_size,
                                       collate_fn=sts_dev_data.collate_fn)

    # Init model.
    config = {'hidden_dropout_prob': args.hidden_dropout_prob,
              'num_labels': num_labels,
              'hidden_size': 768,
              'data_dir': '.',
              'fine_tune_mode': args.fine_tune_mode}

    config = SimpleNamespace(**config)

    model = MultitaskBERT(config)
    # model = model.to(device)
    
    if args.model_path is not None:
        print('Loading previously trained model')
        saved_model = torch.load(args.model_path)
        model.load_state_dict(saved_model['model'])
        
    model = model.to(device)
    lr = args.lr
    optimizer = AdamW(model.parameters(), lr=lr)
    best_dev_acc = 0

    # Run for the specified number of epochs.
    print('Training sentiment analysis')
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        for batch in tqdm(sst_train_dataloader, desc=f'train-{epoch}', disable=TQDM_DISABLE):
            b_ids, b_mask, b_labels = (batch['token_ids'],
                                       batch['attention_mask'], batch['labels'])

            b_ids = b_ids.to(device)
            b_mask = b_mask.to(device)
            b_labels = b_labels.to(device)

            optimizer.zero_grad()
            logits = model.predict_sentiment(b_ids, b_mask)
            # loss = F.cross_entropy(logits, b_labels.view(-1), reduction='sum') / args.sst_batch_size
            loss = sst_loss(logits, b_labels, args)

            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        train_acc, train_f1, *_ = model_eval_sst(sst_train_dataloader, model, device)
        dev_acc, dev_f1, *_ = model_eval_sst(sst_dev_dataloader, model, device)

        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer, args, config, args.filepath)

        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")


    optimizer = AdamW(model.parameters(), lr=lr)
    best_dev_acc = 0

    # Run for the specified number of epochs.
    print('Training paraphrase')
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        # for batch in tqdm(para_train_dataloader, desc=f'train-{epoch}', disable=TQDM_DISABLE):
        for _ in tqdm(range(1000)):
            batch = next(iter(para_train_dataloader))
            b_ids_1, b_mask_1, b_ids_2, b_mask_2, b_labels = (batch['token_ids_1'],
                                       batch['attention_mask_1'], batch['token_ids_2'], 
                                       batch['attention_mask_2'], batch['labels'])

            b_ids_1 = b_ids_1.to(device)
            b_mask_1 = b_mask_1.to(device)
            b_ids_2 = b_ids_2.to(device)
            b_mask_2 = b_mask_2.to(device)
            b_labels = b_labels.to(device)

            optimizer.zero_grad()
            logits = model.predict_paraphrase(b_ids_1, b_mask_1, b_ids_2, b_mask_2)
            
            # loss = F.binary_cross_entropy(torch.sigmoid(torch.squeeze(logits)).float(), b_labels.view(-1).float(), reduction='sum') / args.para_batch_size
            loss = para_loss(logits, b_labels, args)

            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        # train_acc, train_f1, *_ = model_eval_para(para_train_dataloader, model, device)       # evaluation on test takes too long
        dev_acc, dev_f1, *_ = model_eval_para(para_dev_dataloader, model, device)

        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer, args, config, args.filepath)

        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")

    optimizer = AdamW(model.parameters(), lr=lr)
    best_dev_acc = 0

    # Run for the specified number of epochs.
    print('Training STS')
    for epoch in range(args.epochs):
        model.train()
        train_loss = 0
        num_batches = 0
        for batch in tqdm(sts_train_dataloader, desc=f'train-{epoch}', disable=TQDM_DISABLE):
            b_ids_1, b_mask_1, b_ids_2, b_mask_2, b_labels = (batch['token_ids_1'],
                                       batch['attention_mask_1'], batch['token_ids_2'], 
                                       batch['attention_mask_2'], batch['labels'])

            b_ids_1 = b_ids_1.to(device)
            b_mask_1 = b_mask_1.to(device)
            b_ids_2 = b_ids_2.to(device)
            b_mask_2 = b_mask_2.to(device)
            b_labels = b_labels.to(device)

            optimizer.zero_grad()
            logits = model.predict_similarity(b_ids_1, b_mask_1, b_ids_2, b_mask_2)            
            # loss = torch.sum(1 - F.cosine_similarity(logits, b_labels.view(-1))) / args.sts_batch_size
            loss = sts_loss(logits, b_labels)

            # loss = F.mse_loss(torch.squeeze(logits.float()), b_labels.view(-1).float())
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            num_batches += 1

        train_loss = train_loss / (num_batches)

        train_acc, train_f1, *_ = model_eval_sts(sts_train_dataloader, model, device)
        dev_acc, dev_f1, *_ = model_eval_sts(sts_dev_dataloader, model, device)

        if dev_acc > best_dev_acc:
            best_dev_acc = dev_acc
            save_model(model, optimizer, args, config, args.filepath)

        print(f"Epoch {epoch}: train loss :: {train_loss :.3f}, train acc :: {train_acc :.3f}, dev acc :: {dev_acc :.3f}")

def test_multitask(args):
    '''Test and save predictions on the dev and test sets of all three tasks.'''
    with torch.no_grad():
        device = torch.device('cuda') if args.use_gpu else torch.device('cpu')
        saved = torch.load(args.filepath)
        config = saved['model_config']

        model = MultitaskBERT(config)
        model.load_state_dict(saved['model'])
        model = model.to(device)
        print(f"Loaded model to test from {args.filepath}")

        sst_test_data, num_labels,para_test_data, sts_test_data = \
            load_multitask_data(args.sst_test,args.para_test, args.sts_test, split='test')

        sst_dev_data, num_labels,para_dev_data, sts_dev_data = \
            load_multitask_data(args.sst_dev,args.para_dev,args.sts_dev,split='dev')

        sst_test_data = SentenceClassificationTestDataset(sst_test_data, args)
        sst_dev_data = SentenceClassificationDataset(sst_dev_data, args)

        sst_test_dataloader = DataLoader(sst_test_data, shuffle=True, batch_size=args.sst_batch_size,
                                         collate_fn=sst_test_data.collate_fn)
        sst_dev_dataloader = DataLoader(sst_dev_data, shuffle=False, batch_size=args.sst_batch_size,
                                        collate_fn=sst_dev_data.collate_fn)

        para_test_data = SentencePairTestDataset(para_test_data, args)
        para_dev_data = SentencePairDataset(para_dev_data, args)

        para_test_dataloader = DataLoader(para_test_data, shuffle=True, batch_size=args.para_batch_size,
                                          collate_fn=para_test_data.collate_fn)
        para_dev_dataloader = DataLoader(para_dev_data, shuffle=False, batch_size=args.para_batch_size,
                                         collate_fn=para_dev_data.collate_fn)

        sts_test_data = SentencePairTestDataset(sts_test_data, args)
        sts_dev_data = SentencePairDataset(sts_dev_data, args, isRegression=True)

        sts_test_dataloader = DataLoader(sts_test_data, shuffle=True, batch_size=args.sts_batch_size,
                                         collate_fn=sts_test_data.collate_fn)
        sts_dev_dataloader = DataLoader(sts_dev_data, shuffle=False, batch_size=args.sts_batch_size,
                                        collate_fn=sts_dev_data.collate_fn)

        dev_sentiment_accuracy,dev_sst_y_pred, dev_sst_sent_ids, \
            dev_paraphrase_accuracy, dev_para_y_pred, dev_para_sent_ids, \
            dev_sts_corr, dev_sts_y_pred, dev_sts_sent_ids = model_eval_multitask(sst_dev_dataloader,
                                                                    para_dev_dataloader,
                                                                    sts_dev_dataloader, model, device)

        test_sst_y_pred, \
            test_sst_sent_ids, test_para_y_pred, test_para_sent_ids, test_sts_y_pred, test_sts_sent_ids = \
                model_eval_test_multitask(sst_test_dataloader,
                                          para_test_dataloader,
                                          sts_test_dataloader, model, device)

        with open(args.sst_dev_out, "w+") as f:
            print(f"dev sentiment acc :: {dev_sentiment_accuracy :.3f}")
            f.write(f"id \t Predicted_Sentiment \n")
            for p, s in zip(dev_sst_sent_ids, dev_sst_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sst_test_out, "w+") as f:
            f.write(f"id \t Predicted_Sentiment \n")
            for p, s in zip(test_sst_sent_ids, test_sst_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.para_dev_out, "w+") as f:
            print(f"dev paraphrase acc :: {dev_paraphrase_accuracy :.3f}")
            f.write(f"id \t Predicted_Is_Paraphrase \n")
            for p, s in zip(dev_para_sent_ids, dev_para_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.para_test_out, "w+") as f:
            f.write(f"id \t Predicted_Is_Paraphrase \n")
            for p, s in zip(test_para_sent_ids, test_para_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sts_dev_out, "w+") as f:
            print(f"dev sts corr :: {dev_sts_corr :.3f}")
            f.write(f"id \t Predicted_Similiary \n")
            for p, s in zip(dev_sts_sent_ids, dev_sts_y_pred):
                f.write(f"{p} , {s} \n")

        with open(args.sts_test_out, "w+") as f:
            f.write(f"id \t Predicted_Similiary \n")
            for p, s in zip(test_sts_sent_ids, test_sts_y_pred):
                f.write(f"{p} , {s} \n")


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sst_train", type=str, default="data/ids-sst-train.csv")
    parser.add_argument("--sst_dev", type=str, default="data/ids-sst-dev.csv")
    parser.add_argument("--sst_test", type=str, default="data/ids-sst-test-student.csv")

    parser.add_argument("--para_train", type=str, default="data/quora-train.csv")
    parser.add_argument("--para_dev", type=str, default="data/quora-dev.csv")
    parser.add_argument("--para_test", type=str, default="data/quora-test-student.csv")

    parser.add_argument("--sts_train", type=str, default="data/sts-train.csv")
    parser.add_argument("--sts_dev", type=str, default="data/sts-dev.csv")
    parser.add_argument("--sts_test", type=str, default="data/sts-test-student.csv")

    parser.add_argument("--seed", type=int, default=11711)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--fine-tune-mode", type=str,
                        help='last-linear-layer: the BERT parameters are frozen and the task specific head parameters are updated; full-model: BERT parameters are updated as well',
                        choices=('last-linear-layer', 'full-model'), default="last-linear-layer")
    parser.add_argument("--use_gpu", action='store_true')

    parser.add_argument("--sst_dev_out", type=str, default="predictions/sst-dev-output.csv")
    parser.add_argument("--sst_test_out", type=str, default="predictions/sst-test-output.csv")

    parser.add_argument("--para_dev_out", type=str, default="predictions/para-dev-output.csv")
    parser.add_argument("--para_test_out", type=str, default="predictions/para-test-output.csv")

    parser.add_argument("--sts_dev_out", type=str, default="predictions/sts-dev-output.csv")
    parser.add_argument("--sts_test_out", type=str, default="predictions/sts-test-output.csv")

    parser.add_argument("--sst_batch_size", help='sst: 64, cfimdb: 8 can fit a 12GB GPU', type=int, default=8)
    parser.add_argument("--para_batch_size", help='sst: 64, cfimdb: 8 can fit a 12GB GPU', type=int, default=64)
    parser.add_argument("--sts_batch_size", help='sst: 64, cfimdb: 8 can fit a 12GB GPU', type=int, default=8)

    parser.add_argument("--hidden_dropout_prob", type=float, default=0.3)
    parser.add_argument("--lr", type=float, help="learning rate", default=1e-5)

    parser.add_argument("--model_path", type=str, default=None)

    parser.add_argument("--file_prefix", type=str, default="")

    parser.add_argument("--train_type", type=str, default=None)

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    args = get_args()
    if args.model_path is not None:
        args.filepath = args.model_path
    else:
        args.filepath = f'{args.file_prefix}{args.fine_tune_mode}-{args.epochs}-{args.lr}-multitask.pt' # Save path.
    seed_everything(args.seed)  # Fix the seed for reproducibility.
    if args.train_type is None:
        train_multitask(args)
    elif args.train_type == "simultaneous":
        train_simultaneous(args)
    elif args.train_type == "pcgrad":
        train_PCGrad(args)
    else:
        print("Not training") 
    test_multitask(args)
