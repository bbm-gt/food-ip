#!/usr/bin/env python3
"""
轻量关键帧提取模块 v1.0
=======================
从餐饮IP课程视频中提取关键画面，供 refine-knowledge-food-ip 进行视觉理解。

策略:
  1. 场景明显变化时截帧（基于帧间直方图差异）
  2. 长时间静态 PPT 去重（相似帧合并）
  3. 视频案例切换时优先保留
  4. 每帧记录 timestamp 与 source_id 关联

依赖: opencv-python (cv2), numpy
安装: pip install opencv-python numpy
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("[warn] opencv-python 未安装，关键帧提取功能不可用。安装: pip install opencv-python numpy")


# ============================================================================
# 配置
# ============================================================================

# 场景变化检测阈值（0-1，越大越不敏感）
SCENE_CHANGE_THRESHOLD = 0.35

# 最小帧间隔（秒），避免同一场景截太多
MIN_FRAME_INTERVAL = 5.0

# 相似帧去重阈值（0-1，越大越严格）
SIMILARITY_DEDUP_THRESHOLD = 0.92

# PPT 静止检测：连续 N 帧相似度超过阈值的帧视为 PPT 静止
PPT_STILL_THRESHOLD = 0.98
PPT_MIN_STILL_DURATION = 8.0  # 秒

# 采样间隔（秒），每隔 N 秒检查一帧
SAMPLE_INTERVAL = 1.0

# 输出 JPEG 质量
JPEG_QUALITY = 85


def extract_keyframes(video_path, output_dir, source_id=None, verbose=False):
    """
    从视频中提取关键帧。

    Args:
        video_path: 视频文件路径
        output_dir: 输出目录（会在其下创建 source_id 子目录）
        source_id: 来源ID，用于命名和 manifest
        verbose: 是否打印详细信息

    Returns:
        list of dict: [{timestamp_sec, image_path, type}, ...]
    """
    if not HAS_CV2:
        print("[error] opencv-python 未安装，无法提取关键帧")
        return []

    video_path = Path(video_path)
    output_dir = Path(output_dir)
    if source_id:
        output_dir = output_dir / source_id
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[error] 无法打开视频: {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps > 0 else 0

    if verbose:
        print(f"  [keyframe] {video_path.name}: {fps:.1f}fps, {duration:.0f}s, {total_frames} frames")

    keyframes = []
    prev_hist = None
    prev_frame_img = None
    prev_timestamp = -MIN_FRAME_INTERVAL * 2  # 确保第一帧可截

    # PPT 静止检测状态
    still_start = None
    still_saved = False

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        timestamp = frame_idx / fps if fps > 0 else 0

        # 按采样间隔跳帧
        if timestamp - prev_timestamp < SAMPLE_INTERVAL:
            frame_idx += max(1, int(SAMPLE_INTERVAL * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            continue

        # 灰度化 + 缩放到 128x72 用于直方图比较
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (128, 72))
        hist = cv2.calcHist([small], [0], None, [64], [0, 256])
        hist = cv2.normalize(hist, hist).flatten()

        if prev_hist is not None:
            similarity = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)

            # 检测 PPT 静止
            if similarity > PPT_STILL_THRESHOLD:
                if still_start is None:
                    still_start = timestamp
                elif timestamp - still_start > PPT_MIN_STILL_DURATION and not still_saved:
                    # PPT 长时间静止，保存一张即可
                    still_saved = True
            else:
                still_start = None
                still_saved = False

            # 场景变化检测
            is_scene_change = similarity < SCENE_CHANGE_THRESHOLD
            is_min_interval = timestamp - prev_timestamp >= MIN_FRAME_INTERVAL

            should_save = False
            frame_type = "scene_change"

            if is_scene_change and is_min_interval:
                should_save = True
                frame_type = "scene_change"
            elif still_saved:
                should_save = True
                frame_type = "ppt_still_capture"

            if should_save:
                # 与所有已有帧去重
                if not _is_duplicate(frame, keyframes, output_dir):
                    ts_str = f"{int(timestamp):06d}"
                    img_name = f"{ts_str}.jpg"
                    img_path = output_dir / img_name
                    cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

                    kf = {
                        "source_id": source_id,
                        "timestamp_sec": int(timestamp),
                        "time_str": f"{int(timestamp//60):02d}:{int(timestamp%60):02d}",
                        "image": str(img_path.relative_to(output_dir.parent.parent))
                               if output_dir.parent.parent.exists() else img_name,
                        "image_path": str(img_path),
                        "type": frame_type,
                        "similarity_to_prev": float(similarity) if prev_hist is not None else None,
                    }
                    keyframes.append(kf)

                    if verbose:
                        print(f"    [keyframe] {kf['time_str']} ({frame_type})")

                    prev_timestamp = timestamp
                    prev_frame_img = frame.copy()

        else:
            # 第一帧总是保存
            ts_str = f"{int(timestamp):06d}"
            img_name = f"{ts_str}.jpg"
            img_path = output_dir / img_name
            cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            keyframes.append({
                "source_id": source_id,
                "timestamp_sec": int(timestamp),
                "time_str": f"{int(timestamp//60):02d}:{int(timestamp%60):02d}",
                "image": img_name,
                "image_path": str(img_path),
                "type": "first_frame",
                "similarity_to_prev": None,
            })
            prev_timestamp = timestamp
            prev_frame_img = frame.copy()

        prev_hist = hist
        frame_idx += max(1, int(SAMPLE_INTERVAL * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

    cap.release()

    if verbose:
        print(f"    [keyframe] 提取完成: {len(keyframes)} 帧")

    return keyframes


def _is_duplicate(frame, existing_keyframes, output_dir):
    """检查帧是否与已有关键帧高度重复"""
    if len(existing_keyframes) < 1:
        return False

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (128, 72))
    hist = cv2.calcHist([small], [0], None, [64], [0, 256])
    hist = cv2.normalize(hist, hist).flatten()

    for kf in existing_keyframes[-5:]:  # 只检查最近 5 张
        kf_path = Path(kf["image_path"])
        if not kf_path.exists():
            continue
        existing = cv2.imread(str(kf_path))
        if existing is None:
            continue
        egray = cv2.cvtColor(existing, cv2.COLOR_BGR2GRAY)
        esmall = cv2.resize(egray, (128, 72))
        ehist = cv2.calcHist([esmall], [0], None, [64], [0, 256])
        ehist = cv2.normalize(ehist, ehist).flatten()
        similarity = cv2.compareHist(hist, ehist, cv2.HISTCMP_CORREL)
        if similarity > SIMILARITY_DEDUP_THRESHOLD:
            return True
    return False


def save_keyframe_manifest(keyframes, manifest_dir, source_id):
    """保存关键帧 manifest"""
    manifest_dir = Path(manifest_dir)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"keyframes_{source_id}.jsonl"

    with open(manifest_path, "w", encoding="utf-8") as f:
        for kf in keyframes:
            entry = {
                "source_id": kf["source_id"],
                "timestamp_sec": kf["timestamp_sec"],
                "image": kf["image"],
                "type": kf["type"],
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return manifest_path


if __name__ == "__main__":
    # 简单测试
    import sys
    if len(sys.argv) > 1:
        test_video = sys.argv[1]
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("./test_keyframes")
        kfs = extract_keyframes(test_video, out, source_id="SRC0001", verbose=True)
        manifest = save_keyframe_manifest(kfs, out, "SRC0001")
        print(f"Done: {len(kfs)} frames → {manifest}")
    else:
        print("用法: python extract_keyframes.py <video_path> [output_dir]")
