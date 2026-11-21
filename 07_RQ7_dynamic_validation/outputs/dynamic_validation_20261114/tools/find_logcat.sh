cd ~/rvsec_runs
for f in $(ls -t */results/*/*/*.logcat 2>/dev/null | head -40); do
  if grep -aq "RVSEC-COV" "$f" && grep -aq " RVSEC *: .*v=1 code=" "$f"; then
    echo "$f"; grep -a -m2 " RVSEC *: .*v=1 code=" "$f" | cut -c1-400; grep -a -m2 "RVSEC-COV" "$f" | cut -c1-300
    d=$(dirname $(dirname "$f")); ls $d; python3 -c "import json;t=json.load(open('$d/tasks.json'))['tasks'][0];print(json.dumps(t)[:1500])"
    break
  fi
done
