# backend 브랜치 — 무엇이 있고, 무엇이 없고, 시험이 왜 그만큼만 도나

2026-08-25

---

## 1. 이 브랜치가 담는 것

**API · DB · 그 시험.** 그것뿐이다.

| 담는다 | 안 담는다 | 어디 있나 |
|---|---|---|
| `app/` (라우터·유스케이스·어댑터·도메인) | `app/static/*` 화면 파일 | `front` 브랜치 |
| `db/` (PostgreSQL 어댑터·마이그레이션) | `docs/submission/*` 제출 정본 | 작업 저장소 |
| `tests/` (위 코드의 시험) | `docs/reports/*` 내부 진단 리포트 | 작업 저장소 |
| `docs/handoff/` 중 **백엔드 계약** | `data/*` 약관 원문·산출물 | **커밋 안 한다**(저작물) |
| `config/*.json` 설정·원장 | 전처리·평가 스크립트 | 작업 저장소 |

---

## 2. ★2026-08-25 에 고친 것 — **시험이 한 건도 안 돌고 있었다**

이 브랜치를 클론해서 `pytest` 를 치면 이렇게 끝났다 —

```
ImportError while loading conftest '.../tests/conftest.py'.
tests/conftest.py:51: from app.main import app
E   ModuleNotFoundError: No module named 'app.ml'
```

`conftest.py` 가 `app.main` 을 임포트하는데 그게 죽으니 **수집 자체가 안 됐다.**
`pytest` 가 0건을 돌고 끝났다. 브랜치가 「시험이 통과한다」고 말할 수도, 
「실패한다」고 말할 수도 없는 상태였다.

원인이 다섯 겹이었다 —

| # | 무엇 | 고침 |
|---|---|---|
| 1 | `app/ml/` 이 없다 (`face_service` 가 임포트) | 작은 `.py` 4개를 넣었다(모델 가중치 아님) |
| 2 | `app/adapters/pg_*` · `app/db/` 가 **옛 위치에 남아 있다** | 지웠다 — 코드는 `db/postgres/` 로 옮겼다 |
| 3 | `db/postgres/pool.py` 등 **새 파일이 빠졌다** | 넣었다 |
| 4 | `app/static/` **디렉터리가 없어 앱이 안 뜬다** | `README.md` 로 자리를 지킨다(§3) |
| 5 | 브랜치에 없는 `scripts.eval/extract/...` 를 시험하는 파일 23개 | 지웠다 — **안 싣는 코드의 시험은 여기 있으면 안 된다** |

★5번은 「실패하는 시험을 지운 것」이 아니다. 그것들은 **수집 단계에서 터져
나머지 600여 건을 통째로 막고 있었다.** 지우니 나머지가 돈다.

---

## 3. `app/static/README.md` 가 왜 있나

`app/main.py` 가 기동할 때 `StaticFiles(directory="app/static")` 를 마운트한다.
디렉터리가 없으면 `RuntimeError` 로 **앱이 아예 안 뜬다.**
git 은 빈 디렉터리를 추적하지 않으므로 파일 하나가 자리를 지킨다.
화면 파일은 여전히 `front` 브랜치 것이다.

---

## 4. ★지금 상태 — **54건은 여전히 실패한다. 감추지 않는다.**

수집은 되고 전체가 완주한다. 그중 **54건이 실패하는데, 전부 같은 이유다** —
**이 브랜치가 일부러 안 담는 것을 찾는다.**

| 무엇을 못 찾나 | 실패 수(대략) | 어디 있나 |
|---|---:|---|
| `app/static/*.js`·`.html` 화면 파일 | 6 | `front` 브랜치 |
| `docs/submission/*` 제출 정본·시각화 | 4 | 작업 저장소 |
| `docs/handoff/*.html` 소유권·검토 화면 | 6 | 작업 저장소 |
| `data/extracted`·`data/structured` 산출물 | 20+ | **커밋 안 한다**(약관 원문 파생물) |
| 얼굴 라이브니스 ONNX 모델 | 4~6 | 배포 시 별도 배치 |
| 문서 별칭 원장 등 데이터 파일 | 4 | 작업 저장소 |

★★**코드 결함이 아니다.** 그렇다고 「통과한다」고 말할 수도 없다.
  둘을 뭉개지 않으려고 여기 적어 둔다.

### 그러면 무엇으로 백엔드를 검증하나

이 브랜치가 **실제로 싣는 코드**의 시험은 전부 통과한다. 예를 들어 —

```bash
pytest tests/test_rerank_process.py tests/test_rerank_worker.py \
       tests/test_metrics.py tests/test_precheck_related.py \
       tests/test_clause_search_route.py tests/test_app_route_snapshots.py -q
# 74 passed
```

### 남은 일

54건을 「실패」가 아니라 **`skip` 으로** 만드는 것이 옳다 —
없는 자원을 찾는 시험은 그 자원이 없으면 건너뛰어야 한다.
지금은 그 표시가 없어서 **없는 것과 틀린 것이 같은 색으로 보인다.**
