"""Application 계층 — 유스케이스와 포트.

의존성 규칙(v3.2 §2, RULE): 이 계층은 FastAPI/LangChain/SQLAlchemy/openai SDK 등 구체
프레임워크를 import하지 않는다(TEST-ARCH-001). 도메인 예외(app.core.errors, 순수)만 참조한다.
어댑터가 포트를 구현하고, 조립(composition)이 주입한다.
"""
