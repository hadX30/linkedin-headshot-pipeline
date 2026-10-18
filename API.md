# Web API

Start the API from the project directory after installing `requirements.txt` and
setting `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

FastAPI's interactive API docs are available at `http://localhost:8000/docs`.

## Run the full pipeline

Send a multipart form upload in the `image` field:

```bash
curl -X POST http://localhost:8000/v1/headshots \
  -F "image=@portrait.jpg"
```

The response includes `job_id`, `status`, analysis and validation data, and
`generated_image`. When a generated image exists, `generated_image` is a URL
such as `/v1/headshots/<job_id>/image`; fetch it with `GET` to retrieve the PNG.
Possible pipeline statuses include `delivered`, `manual_review`, and `error`.

`GET /health` returns `{"status":"ok"}`. Each request runs synchronously, so
the POST remains open until analysis, generation, and validation finish. The
API does not include authentication; put it behind the host application's
authentication and request controls before exposing it publicly.
