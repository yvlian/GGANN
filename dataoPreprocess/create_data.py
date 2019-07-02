import json
import glob
import os

with open('./valid_idx.json', 'r', encoding='utf-8') as vif:
    valid_idx = json.load(vif)['valid_idxs']
train_graphs = []
valid_graphs = []

for graph_path in glob.glob('../graph/*.graph'):

    n = 0
    for path in glob.glob('../data/*/'):
        if os.path.exists(path + graph_path.split('/')[2].split('.')[0] + '.ast'):
            break
        n += 1
    targets = [[0] for _ in range(18)]
    targets[n][0] = 1
    with open(graph_path, 'r', encoding='utf-8') as gf:
        graph = json.load(gf)
        edges = graph['graph_edges']
        nodes = graph['nodes_feature']
    if graph_path.split('/')[2].split('.')[0] in valid_idx:
        valid_graphs.append({"targets": targets, "graph": edges, "node_features": nodes})
    else:
        train_graphs.append({"targets": targets, "graph": edges, "node_features": nodes})

with open('./train_graphs.json', 'w', encoding='utf-8') as tf:
    json.dump(train_graphs, tf)

with open('./valid_graphs.json', 'w', encoding='utf-8') as vf:
    json.dump(valid_graphs, vf)
