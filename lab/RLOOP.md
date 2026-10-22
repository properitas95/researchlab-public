# rloop: 작은 연구 루프

한 번의 시행은 파일 하나를 바꾸고, 고정된 평가기를 한 번 돌리고, `results.tsv`에 한 줄을 남긴다. 바꾼 파일은 `runs/<n>/`에 그대로 복사되므로 버린 아이디어도 다시 읽을 수 있다.

왜 이렇게 작게 만들었나. 루프는 장치가 적을수록 많이 돈다. 강제 계층과 문서가 붙으면 시행이 줄어든다. 원형은 Karpathy autoresearch의 고정 평가기·keep/discard·TSV다. 확인 표본 1회 잠금은 Sakana AI-Scientist-v3 최종 평가의 재실행 거부에서, 평가 계약을 해시로 묶는 방식은 autoresearchlab의 사전등록 계약 ID에서, 연속 crash 상한은 AI-Scientist-v2의 디버그 깊이 상한에서 가져왔다. 그 밖의 다중 에이전트·트리 탐색·논문 작성 층은 가져오지 않았다.

## 세 층

| 층 | 한 단위 | 여기서 쓰는 것 | 기록 |
|---|---|---|---|
| 시행 | 바꾸는 파일 하나 수정 → 평가 1회 | `rloop run` | `results.tsv` 한 줄 |
| 질문 | 시행 묶음의 판정, 확인 표본 1회 | `rloop holdout`, `rloop decide` | `.rloop/holdout_*.json`, `DECISIONS.md` |
| 절차(RRSI) | 루프를 돌리는 방식끼리 비교 | `--procedure` 꼬리표 | `status`의 절차별 시행·keep 수 |

## 명령

```
rloop.py init    DIR --q Q1 --eval CMD --frozen F.. --mutable F.. [--holdout CMD] [--min-delta 0.01]
rloop.py run     DIR --q Q1 -m "무엇을 바꿨나" [--procedure v0]
rloop.py holdout DIR --q Q1
rloop.py revert  DIR --q Q1
rloop.py status  DIR
rloop.py decide  DIR --q Q1 --verdict invalid|no_improvement|inconclusive|supported_within_scope -m "이유"
rloop.py selftest
```

평가기는 표준출력 마지막 JSON 줄에 `{"score": 수, "pass": 참/거짓}`을 낸다. 나머지 키는 로그에만 남는다.

## 코드가 막는 것

- 고정 파일의 해시가 바뀌면 `run`과 `holdout`이 거부한다. 평가 기준을 바꾸려면 새 질문 ID로 `init`한다.
- 확인 표본(`holdout`)은 질문당 한 번만 열린다. 열 때 개발 최고안과 기준선(첫 keep)을 함께 평가해, 확인 표본에서도 비교가 남는다.
- 직전 최고보다 `min_delta`를 넘어야 keep이다. 연속 3회 crash나 `stop_after`회 무채택이면 STOP을 출력한다.
- 시행은 별도 프로세스 그룹에서 돌고 시간 초과 시 그룹째 종료한다. 상태 값은 keep, discard, invalid, crash, holdout이다.

- 같은 폴더의 `run`·`holdout`은 잠금으로 차례로 돈다. 병렬 제안자를 쓰려면 질문마다 폴더를 나눈다.

관례로만 두는 것: 첫 시행은 기준선(holdout의 비교 대상이고 절차 집계에서 빠진다), 시행 하나에 아이디어 하나, 음성 결과도 지우지 않기, 코드를 줄이면서 동률이면 그쪽을 택하기.

## 기존 자산과의 경계

| 필요 | 쓰는 곳 |
|---|---|
| 근거·논지 판정 | 작업 공간의 지식 그래프와 장부 |
| 원고 독립 검토 | 별도 검토 단계(패널 리뷰어) |
| 연구 입력 데이터 | 동결 스냅숏 + 매니페스트 |
| 계산 함수 | 검증된 구현을 import한다. 실험마다 다시 쓰지 않는다 |

## 보류와 착수 조건

- RRSI 바깥 루프(절차 후보를 자동 생성·채택): 지금은 꼬리표별 집계만 있다. 같은 평가기로 50회 이상 시행이 쌓이고, 채점이 코드로 결정될 때 착수한다.
- 공용 계산 모듈: 같은 함수가 두 번째 프로젝트에서 필요할 때 추출한다.
- 외부 엔진(RD-Agent, Sakana v3): 한 질문을 같은 예산으로 비교할 때만 붙인다.

이 저장소의 실험 엔진이다. 전체 흐름은 `../README.md`.
