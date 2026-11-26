from fastapi import FastAPI

app = FastAPI(title="Vegapunk")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
