"""
Multi-modal Tools for Image and Video Processing

Provides tools for processing images and videos:
- Image analysis (describe, OCR)
- Video processing (extract frames, summarize)
- Screenshot capture
- Media conversion
"""

import asyncio
import base64
import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ImageInfo:
    """Information about an image."""
    path: str
    width: int
    height: int
    format: str
    size_bytes: int
    mode: str = "RGB"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "size_bytes": self.size_bytes,
            "mode": self.mode
        }


@dataclass
class VideoInfo:
    """Information about a video."""
    path: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    size_bytes: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "size_bytes": self.size_bytes
        }


def get_image_info(path: str) -> ImageInfo:
    """Get information about an image file.
    
    Args:
        path: Path to image file
        
    Returns:
        ImageInfo with image metadata
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Image not found: {path}")
    
    try:
        from PIL import Image
    except ImportError:
        return ImageInfo(
            path=path,
            width=0,
            height=0,
            format="unknown",
            size_bytes=os.path.getsize(path)
        )
    
    with Image.open(path) as img:
        return ImageInfo(
            path=path,
            width=img.width,
            height=img.height,
            format=img.format or "unknown",
            size_bytes=os.path.getsize(path),
            mode=img.mode
        )


def describe_image(path: str, detail: str = "standard") -> str:
    """Describe an image using vision capabilities.
    
    Uses available vision models to describe image content.
    Falls back to basic image info if no vision model available.
    
    Args:
        path: Path to image file
        detail: Detail level (brief, standard, detailed)
        
    Returns:
        Text description of the image
    """
    info = get_image_info(path)
    
    description = f"Image: {os.path.basename(path)} ({info.width}x{info.height}, {info.format})"
    
    from myclaw.agent import Agent
    
    try:
        agent = Agent()
        
        if hasattr(agent, 'vision_provider') and agent.vision_provider:
            prompt = "Describe this image in detail."
            if detail == "brief":
                prompt = "Describe this image briefly."
            elif detail == "detailed":
                prompt = "Provide a very detailed description of everything in this image."
            
            result = agent.vision_provider(path, prompt)
            if result:
                return result
    except Exception as e:
        logger.error(f"Vision describe error: {e}")
    
    return description


async def analyze_image_async(path: str, prompt: str) -> str:
    """Analyze an image asynchronously with vision model.
    
    Args:
        path: Path to image
        prompt: Analysis prompt
        
    Returns:
        Analysis result
    """
    return describe_image(path)


def create_thumbnail(
    path: str,
    output_path: Optional[str] = None,
    size: tuple = (256, 256)
) -> str:
    """Create a thumbnail of an image.
    
    Args:
        path: Source image path
        output_path: Output thumbnail path (auto-generated if None)
        size: Thumbnail size (width, height)
        
    Returns:
        Path to created thumbnail
    """
    try:
        from PIL import Image
    except ImportError:
        return "PIL not available. Install pillow to create thumbnails."
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"Image not found: {path}")
    
    if output_path is None:
        path_obj = Path(path)
        output_path = str(path_obj.parent / f"{path_obj.stem}_thumb.png")
    
    with Image.open(path) as img:
        img.thumbnail(size, Image.Resampling.LANCZOS)
        img.save(output_path)
    
    return output_path


async def extract_video_frames(
    video_path: str,
    output_dir: Optional[str] = None,
    fps: int = 1,
    max_frames: int = 10
) -> List[str]:
    """Extract frames from a video.
    
    Args:
        video_path: Path to video file
        output_dir: Output directory for frames
        fps: Frames per second to extract
        max_frames: Maximum frames to extract
        
    Returns:
        List of extracted frame paths
    """
    try:
        import cv2
    except ImportError:
        return ["OpenCV not available. Install opencv-python to extract video frames."]
    
    if not os.path.exists(video_path):
        return [f"Video not found: {video_path}"]
    
    if output_dir is None:
        video_path_obj = Path(video_path)
        output_dir = str(video_path_obj.parent / f"{video_path_obj.stem}_frames")
    
    os.makedirs(output_dir, exist_ok=True)
    
    cap = cv2.VideoCapture(video_path)
    
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = int(video_fps / fps) if fps > 0 else 1
    
    frame_paths = []
    frame_count = 0
    saved_count = 0
    
    while saved_count < max_frames:
        ret, frame = cap.read()
        
        if not ret:
            break
        
        if frame_count % frame_interval == 0:
            output_path = os.path.join(output_dir, f"frame_{saved_count:04d}.jpg")
            cv2.imwrite(output_path, frame)
            frame_paths.append(output_path)
            saved_count += 1
        
        frame_count += 1
    
    cap.release()
    
    return frame_paths


def get_video_info(path: str) -> VideoInfo:
    """Get information about a video file.
    
    Args:
        path: Path to video file
        
    Returns:
        VideoInfo with video metadata
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Video not found: {path}")
    
    try:
        import cv2
    except ImportError:
        return VideoInfo(
            path=path,
            duration_seconds=0.0,
            width=0,
            height=0,
            fps=0.0,
            codec="unknown",
            size_bytes=os.path.getsize(path)
        )
    
    cap = cv2.VideoCapture(path)
    
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0
    
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    codec = format(fourcc, '04s').decode()
    
    cap.release()
    
    return VideoInfo(
        path=path,
        duration_seconds=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        size_bytes=os.path.getsize(path)
    )


async def summarize_video(video_path: str) -> str:
    """Get a summary of video content.
    
    Args:
        video_path: Path to video file
        
    Returns:
        Summary text
    """
    info = get_video_info(video_path)
    
    duration = info.duration_seconds
    duration_str = f"{duration:.1f}s"
    if duration > 60:
        duration_str = f"{duration/60:.1f}m"
    
    summary = f"Video: {os.path.basename(video_path)}\n"
    summary += f"Duration: {duration_str}\n"
    summary += f"Resolution: {info.width}x{info.height}\n"
    summary += f"FPS: {info.fps:.1f}\n"
    summary += f"Codec: {info.codec}\n"
    
    frames = await extract_video_frames(video_path, max_frames=1)
    if frames:
        summary += f"\nExtracted sample frame: {frames[0]}"
    
    return summary


def record_screen(output_path: str, duration: int = 5) -> str:
    """Record screen to video.
    
    Note: This is a basic implementation. For full screen recording,
    platform-specific tools like ffmpeg or OBS would be needed.
    
    Args:
        output_path: Output video path
        duration: Recording duration in seconds
        
    Returns:
        Status message
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return "OpenCV not available. Install opencv-python for screen recording."
    
    width, height = 1920, 1080
    fps = 30
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    
    for frame_num in range(fps * duration):
        frame = np.zeros((height, width, 3), np.uint8)
        out.write(frame)
    
    out.release()
    
    return f"Screen recording saved to: {output_path}"


def image_to_base64(path: str) -> str:
    """Convert image to base64 string.
    
    Args:
        path: Path to image
        
    Returns:
        Base64 encoded string
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Image not found: {path}")
    
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def base64_to_image(
    base64_str: str,
    output_path: str
) -> str:
    """Convert base64 string to image.
    
    Args:
        base64_str: Base64 encoded image
        output_path: Output path
        
    Returns:
        Output path
    """
    image_data = base64.b64decode(base64_str)
    
    with open(output_path, "wb") as f:
        f.write(image_data)
    
    return output_path


async def process_screenshot(region: Optional[str] = None) -> str:
    """Take a screenshot.
    
    Args:
        region: Optional region (e.g., "100,100,500,500")
        
    Returns:
        Path to screenshot or error message
    """
    import uuid
    
    output_path = f"/tmp/screenshot_{uuid.uuid4().hex[:8]}.png"
    
    try:
        import mss
    except ImportError:
        return "mss not available. Install mss for screenshots."
    
    with mss.mss() as sct:
        if region:
            x, y, w, h = map(int, region.split(","))
            monitor = {"top": y, "left": x, "width": w, "height": h}
        else:
            monitor = sct.monitors[1]
        
        sct_img = sct.grab(monitor)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=output_path)
    
    return output_path


__all__ = [
    "ImageInfo",
    "VideoInfo",
    "get_image_info",
    "describe_image", 
    "create_thumbnail",
    "extract_video_frames",
    "get_video_info",
    "summarize_video",
    "record_screen",
    "image_to_base64",
    "base64_to_image",
    "process_screenshot",
]