from fastapi import FastAPI

app = FastAPI(title="Task Management API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
