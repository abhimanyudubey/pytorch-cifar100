# train.py
#!/usr/bin/env	python3

""" train network using pytorch

author baiyu
"""

import os
import argparse
import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable

from tensorboardX import SummaryWriter

from conf import settings
from utils import \
    get_network, get_training_dataloader, get_test_dataloader, WarmUpLR


def train(epoch):

    net.train()
    a_net.train()

    for batch_index, (images, labels) in enumerate(cifar100_training_loader):
        if epoch <= args.warm:
            warmup_scheduler.step()

        images = Variable(images)
        labels = Variable(labels)

        labels = labels.cuda()
        images = images.cuda()

        optimizer.zero_grad()
        outputs = net(images)
        weights = a_net(images)
        weights = torch.flatten(weights)

        loss_function = torch.nn.CrossEntropyLoss(reduction='none')
        reg_loss = torch.nn.CrossEntropyLoss()

        loss = loss_function(outputs, labels)
        r_loss = reg_loss(outputs, labels)

        main_loss = (
            epoch*1.0/settings.EPOCH)**0.5 * loss * weights.detach() +\
            (1 - (epoch*1.0/settings.EPOCH)**0.5) * loss
        main_loss.mean().backward()

        adv_loss = loss.detach() * weights
        adv_loss = -10.0*(
            adv_loss.mean() - r_loss.data.clone()
            )/(r_loss.data.clone())
        adv_loss.backward()

        optimizer.step()

        w_loss = adv_loss.item()

        n_iter = (epoch - 1) * len(cifar100_training_loader) + batch_index + 1

        last_layer = list(net.children())[-1]
        for name, para in last_layer.named_parameters():
            if 'weight' in name:
                writer.add_scalar(
                    'LastLayerGradients/grad_norm2_weights',
                    para.grad.norm(), n_iter)
            if 'bias' in name:
                writer.add_scalar(
                    'LastLayerGradients/grad_norm2_bias',
                    para.grad.norm(), n_iter)

        print(
            'Training Epoch: {epoch} [{trained_samples}/{total_samples}]\
            \tLoss: {:0.4f}\t WA: {:0.4f}\tLR: {:0.6f}'.format(
                loss.mean().item(),
                w_loss,
                optimizer.param_groups[0]['lr'],
                epoch=epoch,
                trained_samples=batch_index * args.b + len(images),
                total_samples=len(cifar100_training_loader.dataset)
            ))

        # update training loss for each iteration
        writer.add_scalar('Train/loss', loss.mean().item(), n_iter)

    for name, param in net.named_parameters():
        layer, attr = os.path.splitext(name)
        attr = attr[1:]
        writer.add_histogram("{}/{}".format(layer, attr), param, epoch)


def eval_training(epoch):
    net.eval()

    test_loss = 0.0   # cost function error
    correct = 0.0

    for (images, labels) in cifar100_test_loader:
        images = Variable(images)
        labels = Variable(labels)

        images = images.cuda()
        labels = labels.cuda()

        outputs = net(images)
        loss_function = torch.nn.CrossEntropyLoss()
        loss = loss_function(outputs, labels)
        test_loss += loss.item()
        _, preds = outputs.max(1)
        correct += preds.eq(labels).sum()

    print('Test set: Average loss: {:.4f}, Accuracy: {:.4f}'.format(
        test_loss / len(cifar100_test_loader.dataset),
        correct.float() / len(cifar100_test_loader.dataset)
    ))
    print()

    # add information to tensorboard
    writer.add_scalar(
        'Test/Average loss',
        test_loss / len(cifar100_test_loader.dataset), epoch)
    writer.add_scalar(
        'Test/Accuracy',
        correct.float() / len(cifar100_test_loader.dataset), epoch)

    return correct.float() / len(cifar100_test_loader.dataset)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-net', type=str, required=True, help='net type')
    parser.add_argument('-gpu', type=bool, default=True, help='use gpu or not')
    parser.add_argument(
        '-w', type=int, default=2, help='number of workers for dataloader')
    parser.add_argument(
        '-b', type=int, default=128, help='batch size for dataloader')
    parser.add_argument(
        '-s', type=bool, default=True, help='whether shuffle the dataset')
    parser.add_argument(
        '-warm', type=int, default=1, help='warm up training phase')
    parser.add_argument(
        '-lr', type=float, default=0.1, help='initial learning rate')
    args = parser.parse_args()

    net = get_network(args, use_gpu=args.gpu)
    a_net = get_network(args, use_gpu=args.gpu, num_classes=1, is_sigmoid=True)

    # data preprocessing:
    cifar100_training_loader = get_training_dataloader(
        settings.CIFAR100_TRAIN_MEAN,
        settings.CIFAR100_TRAIN_STD,
        num_workers=args.w,
        batch_size=args.b,
        shuffle=args.s
    )

    cifar100_test_loader = get_test_dataloader(
        settings.CIFAR100_TRAIN_MEAN,
        settings.CIFAR100_TRAIN_STD,
        num_workers=args.w,
        batch_size=args.b,
        shuffle=args.s
    )

    optimizer = optim.SGD(
        list(net.parameters()) + list(a_net.parameters()),
        lr=args.lr, momentum=0.9, weight_decay=5e-4)
    train_scheduler = optim.lr_scheduler.MultiStepLR(
        optimizer, milestones=settings.MILESTONES, gamma=0.2)
    # learning rate decay
    iter_per_epoch = len(cifar100_training_loader)
    warmup_scheduler = WarmUpLR(optimizer, iter_per_epoch * args.warm)
    checkpoint_path = os.path.join(
        settings.CHECKPOINT_PATH, args.net, settings.TIME_NOW)

    # use tensorboard
    if not os.path.exists(settings.LOG_DIR):
        os.mkdir(settings.LOG_DIR)
    writer = SummaryWriter(logdir=os.path.join(
            settings.LOG_DIR, args.net, settings.TIME_NOW))
    input_tensor = torch.Tensor(12, 3, 32, 32).cuda()
    writer.add_graph(net, Variable(input_tensor, requires_grad=True))

    # create checkpoint folder to save model
    if not os.path.exists(checkpoint_path):
        os.makedirs(checkpoint_path)
    checkpoint_path = os.path.join(checkpoint_path, '{net}-{epoch}-{type}.pth')

    best_acc = 0.0
    for epoch in range(1, settings.EPOCH):
        if epoch > args.warm:
            train_scheduler.step(epoch)

        train(epoch)
        acc = eval_training(epoch)

        if epoch > settings.MILESTONES[1] and best_acc < acc:
            torch.save(
                net.state_dict(),
                checkpoint_path.format(net=args.net, epoch=epoch, type='best'))
            best_acc = acc
            continue

        if not epoch % settings.SAVE_EPOCH:
            torch.save(
                net.state_dict(),
                checkpoint_path.format(
                    net=args.net, epoch=epoch, type='regular'))

    writer.close()
