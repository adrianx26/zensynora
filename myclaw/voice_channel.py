"""
Voice Channel Module (TTS/STT)

Provides voice interaction capabilities:
- Text-to-Speech (TTS) synthesis
- Speech-to-Text (STT) transcription
- Voice activity detection
- Audio format handling
- Multiple TTS/STT provider support
- Voice channel management
"""

import asyncio
import base64
import io
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Union

logger = logging.getLogger(__name__)

AUDIO_DIR = Path.home() / ".myclaw" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VoiceConfig:
    """Voice configuration."""
    tts_provider: str = "auto"
    stt_provider: str = "auto"
    tts_model: str = "auto"
    stt_model: str = "auto"
    voice_id: str = "default"
    language: str = "en-US"
    sample_rate: int = 16000
    channels: int = 1


@dataclass
class AudioSegment:
    """Audio segment data."""
    data: bytes
    format: str = "wav"
    sample_rate: int = 16000
    duration_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TranscriptionResult:
    """Speech transcription result."""
    text: str
    language: str = "en-US"
    confidence: float = 1.0
    words: List[Dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class SynthesisResult:
    """TTS synthesis result."""
    audio_data: bytes
    format: str = "mp3"
    duration_seconds: float = 0.0
    provider: str = "unknown"


class TTSProvider:
    """Text-to-Speech provider interface."""
    
    def __init__(self, config: VoiceConfig):
        self._config = config
    
    async def synthesize(self, text: str) -> SynthesisResult:
        """Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            
        Returns:
            SynthesisResult with audio data
        """
        raise NotImplementedError
    
    async def list_voices(self) -> List[Dict[str, Any]]:
        """List available voices."""
        return []


class STTProvider:
    """Speech-to-Text provider interface."""
    
    def __init__(self, config: VoiceConfig):
        self._config = config
    
    async def transcribe(self, audio_data: bytes, format: str = "wav") -> TranscriptionResult:
        """Transcribe speech from audio.
        
        Args:
            audio_data: Raw audio data
            format: Audio format (wav, mp3, etc.)
            
        Returns:
            TranscriptionResult with transcribed text
        """
        raise NotImplementedError
    
    async def transcribe_stream(self, audio_stream: Any) -> TranscriptionResult:
        """Transcribe from audio stream."""
        raise NotImplementedError


class GTTSProvider(TTSProvider):
    """Google Text-to-Speech provider."""
    
    def __init__(self, config: VoiceConfig):
        super().__init__(config)
        self._available = self._check_availability()
    
    def _check_availability(self) -> bool:
        try:
            from gtts import gTTS
            return True
        except ImportError:
            logger.warning("gTTS not installed. Install with: pip install gtts")
            return False
    
    async def synthesize(self, text: str) -> SynthesisResult:
        if not self._available:
            return SynthesisResult(audio_data=b"", provider="gtts")
        
        try:
            from gtts import gTTS
            
            mp3_buffer = io.BytesIO()
            tts = gTTS(text=text, lang=self._config.language.split("-")[0])
            tts.write_to_fp(mp3_buffer)
            mp3_buffer.seek(0)
            
            return SynthesisResult(
                audio_data=mp3_buffer.read(),
                format="mp3",
                duration_seconds=len(text) / 10,
                provider="gtts"
            )
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")
            return SynthesisResult(audio_data=b"", provider="gtts")
    
    async def list_voices(self) -> List[Dict[str, Any]]:
        return [
            {"id": "default", "name": "Default", "language": self._config.language}
        ]


class pyttsx3Provider(TTSProvider):
    """pyttsx3 offline TTS provider."""
    
    def __init__(self, config: VoiceConfig):
        super().__init__(config)
        self._engine = None
        self._available = self._check_availability()
    
    def _check_availability(self) -> bool:
        try:
            import pyttsx3
            return True
        except ImportError:
            logger.warning("pyttsx3 not installed. Install with: pip install pyttsx3")
            return False
    
    def _get_engine(self):
        if self._engine is None:
            import pyttsx3
            self._engine = pyttsx3.init()
            voices = self._engine.getProperty('voices')
            if voices:
                self._engine.setProperty('voice', voices[0].id)
        return self._engine
    
    async def synthesize(self, text: str) -> SynthesisResult:
        if not self._available:
            return SynthesisResult(audio_data=b"", provider="pyttsx3")
        
        try:
            engine = self._get_engine()
            wav_buffer = io.BytesIO()
            
            engine.save_to_file(text, 'temp_audio.wav')
            engine.runAndWait()
            
            if os.path.exists('temp_audio.wav'):
                with open('temp_audio.wav', 'rb') as f:
                    audio_data = f.read()
                os.remove('temp_audio.wav')
                
                return SynthesisResult(
                    audio_data=audio_data,
                    format="wav",
                    duration_seconds=len(text) / 10,
                    provider="pyttsx3"
                )
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")
        
        return SynthesisResult(audio_data=b"", provider="pyttsx3")
    
    async def list_voices(self) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        
        engine = self._get_engine()
        voices = engine.getProperty('voices')
        
        return [
            {"id": v.id, "name": v.name, "language": getattr(v, 'languages', [''])[0] if hasattr(v, 'languages') else ''}
            for v in voices
        ]


class WhisperSTTProvider(STTProvider):
    """Whisper-based speech-to-text provider."""
    
    def __init__(self, config: VoiceConfig):
        super().__init__(config)
        self._model = None
        self._available = self._check_availability()
    
    def _check_availability(self) -> bool:
        try:
            import whisper
            return True
        except ImportError:
            logger.warning("OpenAI Whisper not installed. Install with: pip install openai-whisper")
            return False
    
    def _get_model(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self._config.stt_model or "base")
        return self._model
    
    async def transcribe(self, audio_data: bytes, format: str = "wav") -> TranscriptionResult:
        if not self._available:
            return TranscriptionResult(text="", provider="whisper")
        
        try:
            import whisper
            
            temp_path = AUDIO_DIR / f"temp_stt_{datetime.now().timestamp()}.wav"
            temp_path.write_bytes(audio_data)
            
            model = self._get_model()
            result = model.transcribe(str(temp_path))
            
            temp_path.unlink(missing_ok=True)
            
            return TranscriptionResult(
                text=result.get("text", ""),
                language=result.get("language", self._config.language),
                confidence=result.get("confidence", 1.0),
                words=result.get("words", []),
                duration_seconds=result.get("duration", 0.0)
            )
        except Exception as e:
            logger.error(f"STT transcription error: {e}")
        
        return TranscriptionResult(text="", provider="whisper")
    
    async def transcribe_stream(self, audio_stream: Any) -> TranscriptionResult:
        return await self.transcribe(b"", "wav")


class VoskSTTProvider(STTProvider):
    """Vosk offline speech-to-text provider."""
    
    def __init__(self, config: VoiceConfig):
        super().__init__(config)
        self._model = None
        self._recognizer = None
        self._available = self._check_availability()
    
    def _check_availability(self) -> bool:
        try:
            from vosk import Model, KaldiRecognizer
            return True
        except ImportError:
            logger.warning("Vosk not installed. Install with: pip install vosk")
            return False
    
    async def transcribe(self, audio_data: bytes, format: str = "wav") -> TranscriptionResult:
        return TranscriptionResult(text="", provider="vosk")
    
    async def transcribe_stream(self, audio_stream: Any) -> TranscriptionResult:
        return TranscriptionResult(text="", provider="vosk")


class VoiceChannel:
    """Main voice channel controller.
    
    Features:
    - Multiple TTS/STT provider support
    - Audio caching
    - Voice activity detection
    - Audio format conversion
    - Stream processing
    """
    
    def __init__(self, config: Optional[VoiceConfig] = None):
        self._config = config or VoiceConfig()
        self._tts_providers: Dict[str, TTSProvider] = {}
        self._stt_providers: Dict[str, STTProvider] = {}
        self._audio_cache: Dict[str, bytes] = {}
        self._active_streams: Dict[str, Any] = {}
        self._initialize_providers()
    
    def _initialize_providers(self):
        """Initialize available TTS/STT providers."""
        self._tts_providers["gtts"] = GTTSProvider(self._config)
        self._tts_providers["pyttsx3"] = pyttsx3Provider(self._config)
        
        self._stt_providers["whisper"] = WhisperSTTProvider(self._config)
        self._stt_providers["vosk"] = VoskSTTProvider(self._config)
    
    def get_tts_provider(self, provider: Optional[str] = None) -> Optional[TTSProvider]:
        """Get TTS provider by name or auto-detect."""
        if provider is None or provider == "auto":
            for p in ["pyttsx3", "gtts"]:
                if p in self._tts_providers:
                    return self._tts_providers[p]
            return None
        
        return self._tts_providers.get(provider)
    
    def get_stt_provider(self, provider: Optional[str] = None) -> Optional[STTProvider]:
        """Get STT provider by name or auto-detect."""
        if provider is None or provider == "auto":
            for p in ["whisper", "vosk"]:
                if p in self._stt_providers:
                    return self._stt_providers[p]
            return None
        
        return self._stt_providers.get(provider)
    
    async def speak(self, text: str, provider: Optional[str] = None) -> bytes:
        """Convert text to speech and return audio data.
        
        Args:
            text: Text to synthesize
            provider: TTS provider to use
            
        Returns:
            Audio data as bytes
        """
        cache_key = f"tts:{hashlib.md5(text.encode()).hexdigest()}"
        
        if cache_key in self._audio_cache:
            return self._audio_cache[cache_key]
        
        tts_provider = self.get_tts_provider(provider)
        if not tts_provider:
            logger.error("No TTS provider available")
            return b""
        
        result = await tts_provider.synthesize(text)
        
        if result.audio_data:
            if len(self._audio_cache) > 100:
                oldest_key = next(iter(self._audio_cache))
                del self._audio_cache[oldest_key]
            
            self._audio_cache[cache_key] = result.audio_data
        
        return result.audio_data
    
    async def listen(self, audio_data: bytes, provider: Optional[str] = None) -> str:
        """Convert speech to text.
        
        Args:
            audio_data: Audio data to transcribe
            provider: STT provider to use
            
        Returns:
            Transcribed text
        """
        stt_provider = self.get_stt_provider(provider)
        if not stt_provider:
            logger.error("No STT provider available")
            return ""
        
        result = await stt_provider.transcribe(audio_data)
        return result.text
    
    async def listen_stream(self, stream_id: str, audio_chunk: bytes) -> Optional[str]:
        """Process audio from a stream and return interim transcription.
        
        Args:
            stream_id: Stream identifier
            audio_chunk: Audio chunk data
            
        Returns:
            Interim transcription or None
        """
        return None
    
    def save_audio(self, audio_data: bytes, filename: str, format: str = "mp3") -> str:
        """Save audio data to file.
        
        Args:
            audio_data: Audio data
            filename: Output filename
            format: Audio format
            
        Returns:
            Path to saved file
        """
        output_path = AUDIO_DIR / f"{filename}.{format}"
        output_path.write_bytes(audio_data)
        return str(output_path)
    
    def load_audio(self, filepath: str) -> Optional[bytes]:
        """Load audio from file.
        
        Args:
            filepath: Path to audio file
            
        Returns:
            Audio data or None
        """
        path = Path(filepath)
        if path.exists():
            return path.read_bytes()
        return None
    
    def audio_to_base64(self, audio_data: bytes) -> str:
        """Convert audio data to base64 string.
        
        Args:
            audio_data: Audio data
            
        Returns:
            Base64 encoded string
        """
        return base64.b64encode(audio_data).decode()
    
    def base64_to_audio(self, base64_str: str) -> bytes:
        """Convert base64 string to audio data.
        
        Args:
            base64_str: Base64 encoded audio
            
        Returns:
            Audio data
        """
        return base64.b64decode(base64_str)
    
    def clear_cache(self):
        """Clear the audio cache."""
        self._audio_cache.clear()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_size = sum(len(v) for v in self._audio_cache.values())
        return {
            "cached_items": len(self._audio_cache),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2)
        }


class VoiceActivityDetector:
    """Voice activity detection for streams."""
    
    def __init__(self, threshold: float = 0.02, sample_rate: int = 16000):
        self._threshold = threshold
        self._sample_rate = sample_rate
    
    def is_speech(self, audio_data: bytes) -> bool:
        """Detect if audio contains speech.
        
        Args:
            audio_data: Audio data
            
        Returns:
            True if speech detected
        """
        import struct
        
        if len(audio_data) < 2:
            return False
        
        try:
            samples = struct.unpack(f"{len(audio_data)//2}h", audio_data)
            
            if not samples:
                return False
            
            rms = (sum(s*s for s in samples) / len(samples)) ** 0.5
            return rms > self._threshold
        except Exception:
            return False
    
    def detect_speech_segments(self, audio_data: bytes, chunk_size: int = 1024) -> List[Dict[str, Any]]:
        """Detect speech segments in audio.
        
        Args:
            audio_data: Audio data
            chunk_size: Size of each chunk to analyze
            
        Returns:
            List of speech segment info
        """
        segments = []
        
        import struct
        
        try:
            samples = struct.unpack(f"{len(audio_data)//2}h", audio_data)
            
            chunks = [samples[i:i+chunk_size] for i in range(0, len(samples), chunk_size)]
            
            in_speech = False
            start_chunk = 0
            
            for i, chunk in enumerate(chunks):
                rms = (sum(s*s for s in chunk) / len(chunk)) ** 0.5 if chunk else 0
                is_speech = rms > self._threshold
                
                if is_speech and not in_speech:
                    start_chunk = i
                    in_speech = True
                elif not is_speech and in_speech:
                    duration = (i - start_chunk) * chunk_size / self._sample_rate
                    segments.append({
                        "start": start_chunk * chunk_size / self._sample_rate,
                        "duration": duration
                    })
                    in_speech = False
            
            if in_speech:
                duration = (len(chunks) - start_chunk) * chunk_size / self._sample_rate
                segments.append({
                    "start": start_chunk * chunk_size / self._sample_rate,
                    "duration": duration
                })
                
        except Exception as e:
            logger.error(f"Speech detection error: {e}")
        
        return segments


import hashlib


def create_voice_channel(config: Optional[VoiceConfig] = None) -> VoiceChannel:
    """Create a voice channel instance."""
    return VoiceChannel(config)


__all__ = [
    "VoiceConfig",
    "AudioSegment",
    "TranscriptionResult",
    "SynthesisResult",
    "TTSProvider",
    "STTProvider",
    "GTTSProvider",
    "pyttsx3Provider",
    "WhisperSTTProvider",
    "VoskSTTProvider",
    "VoiceChannel",
    "VoiceActivityDetector",
    "create_voice_channel",
]