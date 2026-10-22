# 빠른 시작: 모델 없이 도는 작은 루프

평가기는 고정, 바꿀 파일은 `candidate.py` 하나다. 점수는 평균제곱오차(작을수록 좋음).

```
cd lab/defaults/examples/quickstart
python3 ../../../rloop.py init . --q Q1 --eval "python3 evaluate.py --split dev" --holdout "python3 evaluate.py --split holdout" \
  --frozen evaluate.py --mutable candidate.py --direction min --min-delta 0.001
python3 ../../../rloop.py run . --q Q1 -m "baseline K=0"
sed -i.bak 's/K = 0.0/K = 0.3/' candidate.py && python3 ../../../rloop.py run . --q Q1 -m "K=0.3"
sed -i.bak 's/K = 0.3/K = 0.5/' candidate.py && python3 ../../../rloop.py run . --q Q1 -m "K=0.5"
python3 ../../../rloop.py revert . --q Q1       # 최고안(K=0.3)으로 되돌린다
python3 ../../../rloop.py holdout . --q Q1      # 확인 표본은 한 번만, 최고안과 기준선을 함께 연다
python3 ../../../rloop.py status .
```

Windows(`sed` 없음)에서는 같은 순서를 `python run_demo.py`가 임시 폴더에서 한 번에 돌린다.

`evaluate.py`를 고치면 다음 `run`이 거부된다(고정 파일 해시). 모델이 제안하는 루프는 `lab.py experiment`가 같은 일을 한다.
