# 이 브랜치에는 화면 파일이 없다

`backend` 브랜치는 **API·DB·시험**만 담는다. 화면(`insurance.html`·`.js` 등)은
`front` 브랜치가 갖는다 — 담당이 갈려 있어 같은 파일을 두 곳에서 고치면 충돌한다.

★그런데 **디렉터리 자체는 있어야 한다.** `app/main.py` 가 기동할 때
`StaticFiles(directory=...)` 로 이 경로를 마운트하는데, 없으면
`RuntimeError: Directory ... does not exist` 로 **앱이 아예 안 뜬다.**
그러면 `tests/conftest.py` 가 `app.main` 을 임포트하다 죽어 **시험이 한 건도 수집되지 않는다.**

그래서 이 파일이 자리를 지킨다(git 은 빈 디렉터리를 추적하지 않는다).
화면을 보려면 `front` 브랜치를 합치거나 그쪽에서 받아 온다.
