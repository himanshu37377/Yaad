class MockClient:
    """Deterministic offline adapter; never used for published evaluation metrics."""
    def reason_json(self, messages, schema, prompt_id):
        return {"status": "not_found", "answer": "Mock client has no recorded fixture.", "evidence_ids": []}
    def vision_extract(self, image_bytes, schema, prompt_id):
        return {"text": "", "fields": []}
    def transcribe(self, audio_path):
        return []
    def describe_photo(self, image_bytes):
        return {"description": "", "tags": []}
