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
    _, _, indexer = services()
    try:
        typer.echo(json.dumps(indexer.index_folder(folder_id), indent=2))
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

@app.command()
def ask(question: str, folder_id: int | None = None):
    _, db, _ = services()
    typer.echo(json.dumps(answer_question(db, question, folder_id), ensure_ascii=False, indent=2))

@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    uvicorn.run("yaad.api.main:app", host=host, port=port, reload=False)

@app.command()
def rebuild_fts():
    _, db, _ = services()
    db.rebuild_fts()
    typer.echo("FTS index rebuilt")

if __name__ == "__main__":
    app()
