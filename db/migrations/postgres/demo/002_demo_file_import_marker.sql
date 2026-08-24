-- 이관 행의 출처 표식을 디렉터리 이름과 혼동되지 않는 용어로 정정한다.
UPDATE demo.submission SET run_id = 'file_import' WHERE run_id = 'legacy';
