"""PiAssistant voice assistant support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import logging

from homeassistant.components import stt
from homeassistant.components.stt import SpeechMetadata
from homeassistant.components.media_player import MediaType
from homeassistant.components.assist_pipeline import (
    AudioSettings,
    PipelineEvent,
    PipelineEventType,
    PipelineNotFound,
    PipelineStage,
    WakeWordSettings,
    async_pipeline_from_audio_stream,
    select as pipeline_select,
)
from homeassistant.components.assist_pipeline.error import (
    WakeWordDetectionAborted,
    WakeWordDetectionError,
)
from homeassistant.core import Context, HomeAssistant

from .media_player import PiAssistantMediaPlayer

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class VoiceAssistantPipeline:
    """Base abstract pipeline class."""

    started = False
    stop_requested = False

    def __init__(
        self,
        hass: HomeAssistant,
        entity: PiAssistantMediaPlayer,
        handle_event: Callable[[PipelineEventType, dict[str, str] | None], None],
        handle_finished: Callable[[], None],
    ) -> None:
        """Initialize the pipeline."""
        self.context = Context()
        self.hass: HomeAssistant = hass
        self.entity: PiAssistantMediaPlayer = entity
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.handle_event = handle_event
        self.handle_finished = handle_finished
        self._tts_done = asyncio.Event()
        self._tts_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """True if the pipeline is started and hasn't been asked to stop."""
        return self.started and (not self.stop_requested)

    def _event_callback(self, event: PipelineEvent) -> None:
        """Handle pipeline events."""
        event_type = event.type
        data_to_send = None
        error = False
        if event_type == PipelineEventType.STT_START:
            self.is_running = True
        elif event_type == PipelineEventType.STT_END:
            assert event.data is not None
            data_to_send = {"text": event.data["stt_output"]["text"]}
        elif event_type == PipelineEventType.INTENT_END:
            assert event.data is not None
            data_to_send = {
                "conversation_id": event.data["intent_output"]["conversation_id"] or "",
            }
        elif event_type == PipelineEventType.TTS_START:
            assert event.data is not None
            data_to_send = {"text": event.data["tts_input"]}
        elif event_type == PipelineEventType.TTS_END:
            assert event.data is not None
            tts_output = event.data["tts_output"]
            if tts_output:
                path = tts_output["url"]
                self.entity.play_media(MediaType.URL, path)
            else:
                # Empty TTS response
                data_to_send = {}
                self._tts_done.set()
        elif event_type == PipelineEventType.WAKE_WORD_END:
            assert event.data is not None
            if not event.data["wake_word_output"]:
                event_type = PipelineEventType.ERROR
                data_to_send = {
                    "code": "no_wake_word",
                    "message": "No wake word detected",
                }
                error = True
        elif event_type == PipelineEventType.ERROR:
            assert event.data is not None
            data_to_send = {
                "code": event.data["code"],
                "message": event.data["message"],
            }
            error = True

        self.handle_event(event_type, data_to_send)
        if error:
            self._tts_done.set()
            self.handle_finished()

    async def run_pipeline(
        self,
        device_id: str,
        start_stage: PipelineStage = PipelineStage.STT,
        conversation_id: str | None = None,
        audio_settings: AudioSettings = None,
        wake_word_settings: WakeWordSettings | None = None,
        wake_word_phrase: str | None = None,
        stt_metadata: SpeechMetadata | None = None,
    ) -> None:
        """Run the Voice Assistant pipeline."""

        _LOGGER.debug("Starting pipeline")

        if wake_word_settings is None:
            wake_word_settings = WakeWordSettings(timeout=5)
        if audio_settings is None:
            audio_settings = AudioSettings()
        if stt_metadata is None:
            stt_metadata = SpeechMetadata(
                language="",
                format=stt.AudioFormats.WAV,
                codec=stt.AudioCodecs.PCM,
                bit_rate=stt.AudioBitRates.BITRATE_16,
                sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                channel=stt.AudioChannels.CHANNEL_MONO,
            )

        try:
            await async_pipeline_from_audio_stream(
                self.hass,
                context=self.context,
                event_callback=self._event_callback,
                stt_metadata=stt_metadata,
                stt_stream=None,
                pipeline_id=pipeline_select.get_chosen_pipeline(
                    self.hass, DOMAIN, self.entity.device_id()
                ),
                conversation_id=conversation_id,
                device_id=device_id,
                tts_audio_output="mp3",
                start_stage=start_stage,
                wake_word_settings=wake_word_settings,
                wake_word_phrase=wake_word_phrase,
                audio_settings=audio_settings,
            )

            # Block until TTS is done sending
            await self._tts_done.wait()

            _LOGGER.debug("Pipeline finished")
        except PipelineNotFound:
            self.handle_event(
                PipelineEventType.ERROR,
                {
                    "code": "pipeline not found",
                    "message": "Selected pipeline not found",
                },
            )
            _LOGGER.warning("Pipeline not found")
        except WakeWordDetectionAborted:
            pass  # Wake word detection was aborted and `handle_finished` is enough.
        except WakeWordDetectionError as e:
            self.handle_event(
                PipelineEventType.ERROR,
                {
                    "code": e.code,
                    "message": e.message,
                },
            )
        finally:
            self.handle_finished()
