import logging
import os
from typing import Any, Union, Dict, Optional, List, AsyncIterable

import vertexai
from dotenv import load_dotenv
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from signalsense_agent.agent import app as adk_app

# Load environment variables from .env file at runtime
load_dotenv()

class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        vertexai.init()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        try:
            logging_client = google_cloud_logging.Client()
            self.logger = logging_client.logger(__name__)
        except Exception:
            logging.warning("Google Cloud Logging client not initialized. Falling back to local logging.")
            self.logger = None
            
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    async def async_stream_query(
        self,
        *,
        message: Union[str, Dict[str, Any]],
        user_id: str = "vais-query-reasoning-engine",
        session_id: Optional[str] = None,
        session_events: Optional[List[Dict[str, Any]]] = None,
        run_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterable[Dict[str, Any]]:
        async for event in super().async_stream_query(
            message=message,
            user_id=user_id,
            session_id=session_id,
            session_events=session_events,
            run_config=run_config,
            **kwargs
        ):
            yield event

    def stream_query(
        self,
        *,
        message: Union[str, Dict[str, Any]],
        user_id: str = "vais-query-reasoning-engine",
        session_id: Optional[str] = None,
        run_config: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        return super().stream_query(
            message=message,
            user_id=user_id,
            session_id=session_id,
            run_config=run_config,
            **kwargs
        )

gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=logs_bucket_name)
        if logs_bucket_name
        else InMemoryArtifactService()
    ),
)
