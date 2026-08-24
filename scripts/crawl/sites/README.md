
## 메리츠화재 — 조사 결과 (어댑터 미완성)

목록  `GET /disclosure/product-announcement/product-list.do`  (AngularJS SPA)
       판매상품목록 / 판매중지상품목록 탭, 분류형(상품종류 -> 보험상품명) / 검색형

데이터는 Angular 스코프 `salPdLst` 에 있고 **DOM 에는 토큰이 없다.**
각 항목 구조(실측):
    ttlNm         상품명
    putupStDdTm   판매개시일 (예: 20260713)
    putupEdDdTm   판매중지일 ('-' 이면 판매중)
    file1         약관 경로   (예: /cu/ctl/202607130834588430199U.pdf)
    file1#[E]     암호화 토큰 (다운로드에 쓰는 값)
    file2/file3/file4 + 각 #[E]

★슬롯 확정: 화면의 `pdfDown(item, fileCnt, ttlNm)` 함수가 직접 알려준다.
    file1 -> "<상품명>약관.pdf"        ← 우리가 받을 것
    file2 -> "<상품명>사업방법서.pdf"
    file3 -> "<상품명>요약서.pdf"
    file4 -> "<상품명>상품설명서.pdf"

실손 상품(판매중) 9건 확인:
    실손의료비보험2605 / (계약전환용) / (재개 및 전환용) / 다이렉트 /
    다이렉트(계약전환용) / 유병력자 / 노후실손 / 유병력자(재개용)2607 / 노후실손(재개용)2607

★막힌 지점 — 다운로드
    `POST /hp/fileDownload.do` (path, id, orgFileName, check=Y) 를 fetch 로 부르면
    `{"resultMsg":""}` 만 온다(PDF 가 아니다).
    `file1` 경로를 정적으로 직접 열어도 SPA 의 HTML(38KB)이 돌아온다.
    -> 브라우저에서 **실제 클릭**으로만 받아지는 것으로 보인다.
       browser_collector 의 다운로드 이벤트 포착으로 재시도해야 한다.
