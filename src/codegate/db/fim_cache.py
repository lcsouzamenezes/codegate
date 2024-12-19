import datetime
import hashlib
import json
import re
from typing import Dict, List, Optional

import structlog
from pydantic import BaseModel

from codegate.config import Config
from codegate.db.models import Alert
from codegate.pipeline.base import AlertSeverity, PipelineContext

logger = structlog.get_logger("codegate")


class CachedFim(BaseModel):

    timestamp: datetime.datetime
    critical_alerts: List[Alert]


class FimCache:

    def __init__(self):
        self.cache: Dict[str, CachedFim] = {}

    def _extract_message_from_fim_request(self, request: str) -> Optional[str]:
        """Extract the user message from the FIM request"""
        try:
            parsed_request = json.loads(request)
        except Exception as e:
            logger.error(f"Failed to extract request message: {request}", error=str(e))
            return None

        if not isinstance(parsed_request, dict):
            logger.warning(f"Expected a dictionary, got {type(parsed_request)}.")
            return None

        messages = [
            message
            for message in parsed_request.get("messages", [])
            if isinstance(message, dict) and message.get("role", "") == "user"
        ]
        if len(messages) != 1:
            logger.warning(f"Expected one user message, found {len(messages)}.")
            return None

        content_message = messages[0].get("content")
        return content_message

    def _match_filepath(self, message: str, provider: str) -> Optional[str]:
        # Try to extract the path from the FIM message. The path is in FIM request as a comment:
        # folder/testing_file.py
        # Path: file3.py
        # // Path: file3.js <-- Javascript
        pattern = r"^(#|//|<!--|--|%|;).*?\b([a-zA-Z0-9_\-\/]+\.\w+)\b"
        matches = re.findall(pattern, message, re.MULTILINE)
        # If no path is found, hash the entire prompt message.
        if not matches:
            return None

        # Extract only the paths (2nd group from the match)
        paths = [match[1] for match in matches]

        # Copilot puts the path at the top of the file. Continue providers contain
        # several paths, the one in which the fim is triggered is the last one.
        if provider == "copilot":
            return paths[0]
        else:
            return paths[-1]

    def _calculate_hash_key(self, message: str, provider: str) -> str:
        """Creates a hash key from the message and includes the provider"""
        filepath = self._match_filepath(message, provider)
        if filepath is None:
            logger.warning("No path found in messages. Creating hash key from message.")
            message_to_hash = f"{message}-{provider}"
        else:
            message_to_hash = f"{filepath}-{provider}"

        logger.debug(f"Message to hash: {message_to_hash}")
        hashed_content = hashlib.sha256(message_to_hash.encode("utf-8")).hexdigest()
        logger.debug(f"Hashed content: {hashed_content}")
        return hashed_content

    def _add_cache_entry(self, hash_key: str, context: PipelineContext):
        """Add a new cache entry"""
        critical_alerts = [
            alert
            for alert in context.alerts_raised
            if alert.trigger_category == AlertSeverity.CRITICAL.value
        ]
        new_cache = CachedFim(
            timestamp=context.input_request.timestamp, critical_alerts=critical_alerts
        )
        self.cache[hash_key] = new_cache
        logger.info(f"Added cache entry for hash key: {hash_key}")

    def _are_new_alerts_present(self, context: PipelineContext, cached_entry: CachedFim) -> bool:
        """Check if there are new alerts present"""
        new_critical_alerts = [
            alert
            for alert in context.alerts_raised
            if alert.trigger_category == AlertSeverity.CRITICAL.value
        ]
        return len(new_critical_alerts) > len(cached_entry.critical_alerts)

    def _is_cached_entry_old(self, context: PipelineContext, cached_entry: CachedFim) -> bool:
        """Check if the cached entry is old"""
        elapsed_seconds = (context.input_request.timestamp - cached_entry.timestamp).total_seconds()
        return elapsed_seconds > Config.get_config().max_fim_hash_lifetime

    def could_store_fim_request(self, context: PipelineContext):
        # Couldn't process the user message. Skip creating a mapping entry.
        message = self._extract_message_from_fim_request(context.input_request.request)
        if message is None:
            logger.warning(f"Couldn't read FIM message: {message}. Will not record to DB.")
            return False

        hash_key = self._calculate_hash_key(message, context.input_request.provider)
        cached_entry = self.cache.get(hash_key, None)
        if cached_entry is None:
            self._add_cache_entry(hash_key, context)
            return True

        if self._is_cached_entry_old(context, cached_entry):
            self._add_cache_entry(hash_key, context)
            return True

        if self._are_new_alerts_present(context, cached_entry):
            self._add_cache_entry(hash_key, context)
            return True

        logger.debug(f"FIM entry already in cache: {hash_key}.")
        return False