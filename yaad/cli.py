import json
import typer
import uvicorn
from yaad.config import get_settings
from yaad.db import Database
from yaad.scan.worker import Indexer
from yaad.query.answer import answer_question

app = typer.Typer(help="Yaad evidence-backed memory")

def services():
    settings = get_settings()
    db = Database(settings.db_path)
    return settings, db, Indexer(db, settings)

@app.command()
def add_folder(path: str, cloud_consent: bool = typer.Option(False, "--cloud-consent", help="Allow eligible file content to be sent to Meta Model API")):
    _, _, indexer = services()
    typer.echo(indexer.add_folder(path, cloud_consent))

@app.command()
def index(folder_id: int | None = None):
    settings, db, indexer = services()
    try:
        result = indexer.index_folder(folder_id)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    # Learn automatically from the new evidence before exiting; the
    # gates inside decide whether any model call is legitimate.
    from yaad.memory.auto import schedule_auto_memory, wait_auto_memory
    if folder_id is not None:
        schedule_auto_memory(db, settings, [folder_id])
    else:
        with db.connect() as con:
            schedule_auto_memory(db, settings,
                                 [row["id"] for row in con.execute("SELECT id FROM folders ORDER BY id")])
    wait_auto_memory()
    typer.echo(json.dumps(result, indent=2))

@app.command()
def ask(question: str, folder_id: int | None = None):
    settings, db, _ = services()
    typer.echo(json.dumps(answer_question(db, question, folder_id, settings), ensure_ascii=False, indent=2))
    from yaad.memory.auto import schedule_conversation_learning, wait_auto_memory
    schedule_conversation_learning(db, settings, folder_id, question)
    wait_auto_memory()

@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    import os

    if host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("YAAD_API_TOKEN"):
        # The API has no per-route auth; exposing it beyond loopback
        # without the shared token would publish indexed files and
        # paid endpoints to the network.
        typer.echo(
            "Error: refusing to bind a non-loopback host without YAAD_API_TOKEN set. "
            "Set YAAD_API_TOKEN and send it as 'Authorization: Bearer [REDACTED]' from clients, "
            "or bind 127.0.0.1 (the default).", err=True)
        raise typer.Exit(2)
    uvicorn.run("yaad.api.main:app", host=host, port=port, reload=False)

@app.command()
def rebuild_fts():
    _, db, _ = services()
    db.rebuild_fts()
    typer.echo("FTS index rebuilt")

if __name__ == "__main__":
    app()
