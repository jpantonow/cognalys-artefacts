IMAGE=phtcosta/rvandroid:0.9.5
eval "$(sed -n '/^others_running()/,/^}/p' /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/queue.sh)"
if o=$(others_running); then echo "BUSY:$o"; else echo "free"; fi
(exec -a "bash /tmp/rvsec_queue_fake.sh" sleep 30) &
sleep 1
if o=$(others_running); then echo "BUSY:$o"; else echo "free"; fi
kill %1
