# -*- coding:utf-8 -*-
# author: Ptzu
# @file: demo_folder.py

import os
import time
import argparse
from glob import glob
import sys
import numpy as np
import pickle
import torch
import torch.optim as optim
from tqdm import tqdm
import yaml

from utils.metric_util import per_class_iu, fast_hist_crop
from dataloader.pc_dataset import get_SemKITTI_label_name
from builder import data_builder, model_builder, loss_builder
from config.config import load_config_data
from dataloader.dataset_semantickitti import get_model_class, collate_fn_BEV
from dataloader.pc_dataset import get_pc_model_class

from utils.load_save_util import load_checkpoint

import warnings

warnings.filterwarnings("ignore")

def get_latest_data_names(base_dir, prefix, suffix=".pkl"):
    data_files = sorted(list(glob(base_dir + "/" + prefix + "_*" + suffix)))
    last_timestamp = sorted(list(set([f.split(prefix)[1].split("_")[1] for f in data_files])))[-1]
    return [f for f in data_files if last_timestamp in f]

INFER_KITTI = True

def build_dataset(dataset_config,
                  data,
                  grid_size=[480, 360, 32]):

    label_mapping = dataset_config["label_mapping"]

    if INFER_KITTI:
        SemKITTI_demo = get_pc_model_class('Custom_KITTI')
    else:
        SemKITTI_demo = get_pc_model_class('Custom_demo')

    demo_pt_dataset = SemKITTI_demo(data, return_ref=True, label_mapping=label_mapping)

    if INFER_KITTI:
        demo_dataset = get_model_class(dataset_config['dataset_type'])(
            demo_pt_dataset,
            grid_size=grid_size,
            fixed_volume_space=dataset_config['fixed_volume_space'],
            max_volume_space=dataset_config['max_volume_space'],
            min_volume_space=dataset_config['min_volume_space'],
            ignore_label=dataset_config["ignore_label"],
        )
    else:
        demo_dataset = get_model_class(dataset_config['dataset_type'])(
            demo_pt_dataset,
            grid_size=grid_size,
            fixed_volume_space=False,#dataset_config['fixed_volume_space'],
            max_volume_space=dataset_config['max_volume_space'],
            min_volume_space=dataset_config['min_volume_space'],
            ignore_label=dataset_config["ignore_label"],
        )
    demo_dataset_loader = torch.utils.data.DataLoader(dataset=demo_dataset,
                                                     batch_size=1,
                                                     collate_fn=collate_fn_BEV,
                                                     shuffle=False,
                                                     num_workers=4)

    return demo_dataset_loader

def main(args, data_dir):
    pytorch_device = torch.device('cuda:0')
    config_path = args.config_path
    configs = load_config_data(config_path)
    dataset_config = configs['dataset_params']
    save_dir = args.save_folder + "/"

    demo_batch_size = 1
    model_config = configs['model_params']
    train_hypers = configs['train_params']

    grid_size = model_config['output_shape']
    num_class = model_config['num_class']
    ignore_label = dataset_config['ignore_label']
    model_load_path = train_hypers['model_load_path']

    SemKITTI_label_name = get_SemKITTI_label_name(dataset_config["label_mapping"])
    unique_label = np.asarray(sorted(list(SemKITTI_label_name.keys())))[1:] - 1
    unique_label_str = [SemKITTI_label_name[x] for x in unique_label + 1]

    my_model = model_builder.build(model_config)
    if os.path.exists(model_load_path):
        my_model = load_checkpoint(model_load_path, my_model)

    my_model.to(pytorch_device)

    demo_dataset_loader = build_dataset(dataset_config, data_dir, grid_size=grid_size)
    with open(dataset_config["label_mapping"], 'r') as stream:
        semkittiyaml = yaml.safe_load(stream)
    inv_learning_map = semkittiyaml['learning_map_inv']

    print(f"Dataset length: {len(demo_dataset_loader)}")

    label_list = []

    my_model.eval()
    with torch.no_grad():
        for i_iter_demo, (_, demo_vox_label, demo_grid, demo_pt_labs, demo_pt_fea) in enumerate(
                demo_dataset_loader):

            # print(i_iter_demo)
            demo_pt_fea_ten = [torch.from_numpy(i).type(torch.FloatTensor).to(pytorch_device) for i in
                              demo_pt_fea]
            demo_grid_ten = [torch.from_numpy(i).to(pytorch_device) for i in demo_grid]
            demo_label_tensor = demo_vox_label.type(torch.LongTensor).to(pytorch_device)

            predict_labels = my_model(demo_pt_fea_ten, demo_grid_ten, demo_batch_size)

            predict_labels = torch.argmax(predict_labels, dim=1)
            predict_labels = predict_labels.cpu().detach().numpy()
            for count, i_demo_grid in enumerate(demo_grid):
                inv_labels = np.vectorize(inv_learning_map.__getitem__)(predict_labels[count, demo_grid[count][:, 0], demo_grid[count][:, 1], demo_grid[count][:, 2]]) 
                inv_labels = inv_labels.astype('uint32') & 0xFFFF
                # print(predict_labels.shape)
                # print(inv_labels.shape)
                if INFER_KITTI:
                    np.save(f"{save_dir}{i_iter_demo:06d}.label", inv_labels)
                else:
                    label_list.append(inv_labels)

    if not INFER_KITTI:
        with open(data_dir.replace("raw", "labels"), "wb") as f:
            pickle.dump(label_list, f)
  

if __name__ == '__main__':
    # Training settings
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-y', '--config_path', default='config/semantickitti.yaml')
    parser.add_argument('--demo-folder', type=str, default='', help='path to the folder containing demo lidar scans', required=True)
    parser.add_argument('--save-folder', type=str, default='', help='path to save your result')
    args = parser.parse_args()
    
    if INFER_KITTI:
        main(args, args.demo_folder)
    else:
        chunk_files = get_latest_data_names(args.demo_folder, "raw_chunk")

        for file in chunk_files:
            print(f"Processing {file}")
            # print(' '.join(sys.argv))
            # print(args)
            main(args, file)
