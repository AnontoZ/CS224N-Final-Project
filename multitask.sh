python multitask_classifier.py --fine-tune-mode full-model --file_prefix 'models/pcgrad_sts_' --epochs 10 --lr 1e-5 --use_gpu --para_batch_size 8 --train_type pcgrad
python prepare_submit.py --file-suffix '_pcgrad_sts'

python multitask_classifier.py --fine-tune-mode full-model --file_prefix 'models/simul_sts_' --epochs 10 --lr 1e-5 --use_gpu --para_batch_size 8 --train_type simultaneous
python prepare_submit.py --file-suffix '_simul_sts'

python multitask_classifier.py --fine-tune-mode full-model --file_prefix 'models/sts_' --epochs 10 --lr 1e-5 --use_gpu --para_batch_size 8
python prepare_submit.py --file-suffix '_sts'
# for loading a model, make sure that we change the random seed 