# 합성 Jev 게이트: 문턱을 과제별로 맞추는 루프

Jev 문턱 루프와 같은 구조를 합성 자료로 보인다(API 키 없음, 모델 호출 없음). 판정기가 확신하면(신뢰도 `max(p,1-p)` ≥ 문턱) 자동 처리하고, 아니면 사람이 본다.
비용 = (5 × 자동 오류 + 사람 검토) / n (docs PROTOCOL §5와 같은 생각). dev/holdout은 항목 id 해시로 나눈다. `gen.py`는 고정 시드로 5과제 × 300항목을 만든다.
과제마다 판정기 강도와 과신 정도가 달라서 하나의 문턱으로는 최적이 안 나온다. 바꾸는 파일은 `policy.json`(과제별 문턱) 하나다.

```
researchlab demo jev        # 아래 순서를 임시 폴더에서 3회 시행으로 돌린다
cd lab/defaults/examples/jev_gate && python3 gen.py
python3 ../../../rloop.py init . --q Q1 --eval "python3 evaluate.py --split dev" --holdout "python3 evaluate.py --split holdout" \
  --frozen evaluate.py data.json --mutable policy.json --direction min --min-delta 0.01
python3 ../../../rloop.py run . --q Q1 -m "baseline"     # policy.json을 고치고 run을 반복
python3 ../../../rloop.py holdout . --q Q1               # 확인 표본은 한 번만
```
