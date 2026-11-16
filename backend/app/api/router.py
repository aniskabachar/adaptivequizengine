from fastapi import APIRouter, Depends
from app.core.rate_limit import llm_rate_limit
from app.api.endpoints import quiz, users, educators, analytics, dag, misconceptions, classes, proctoring
from app.api.routes import socratic, explanation, review, socratic_advanced

api_router = APIRouter()

# Routers whose endpoints call the LLM get a per-client request limit
_llm_limited = [Depends(llm_rate_limit)]
api_router.include_router(quiz.router, prefix="/quiz", tags=["quiz"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(educators.router, prefix="/educators", tags=["educators"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(dag.router, dependencies=_llm_limited, prefix="/dag", tags=["dag"])
api_router.include_router(misconceptions.router, prefix="/misconceptions", tags=["misconceptions"])
api_router.include_router(classes.router, prefix="/classes", tags=["classes"])
api_router.include_router(proctoring.router, prefix="/proctoring", tags=["proctoring"])
api_router.include_router(socratic.router, dependencies=_llm_limited, prefix="/socratic", tags=["socratic"])
api_router.include_router(socratic_advanced.router, dependencies=_llm_limited, prefix="/socratic-advanced", tags=["socratic-advanced"])
api_router.include_router(explanation.router, dependencies=_llm_limited, prefix="/explanation", tags=["explanation"])
api_router.include_router(review.router, prefix="/review", tags=["review"])
