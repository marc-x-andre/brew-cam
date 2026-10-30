def build_compose_command(display_source, rtsp_url, overlay_text_path, stream_config, snapshot_path, youtube_target=None):
    width, height, fps = stream_config["width"], stream_config["height"], stream_config["fps"]
    mode = display_source["mode"]
    command = ["ffmpeg"]
    if mode == "camera":
        command += ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", rtsp_url]
    elif mode == "image":
        command += ["-loop", "1", "-framerate", str(fps), "-i", display_source["path"]]
    else:
        command += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]

    if youtube_target is not None:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    filter_graph = (
        f"[0:v]scale={width}:{height},"
        f"drawtext=textfile={overlay_text_path}:reload=1:expansion=none:fontcolor=white:fontsize=24:"
        "x=20:y=20:box=1:boxcolor=black@0.5[comp]"
    )
    command += ["-filter_complex", filter_graph]
    command += ["-map", "[comp]", "-an", "-pix_fmt", "yuv420p", "-r", "1/5", "-update", "1", "-y", str(snapshot_path)]

    if youtube_target is not None:
        bitrate = stream_config.get("bitrate_kbps", 3000)
        gop = str(2 * fps)
        command += [
            "-map", "[comp]", "-map", "1:a",
            "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-b:v", f"{bitrate}k", "-minrate", f"{bitrate}k", "-maxrate", f"{bitrate}k",
            "-bufsize", f"{bitrate * 2}k",
            "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-bf", "2",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "flv", youtube_target.rtmps_url,
        ]
    return command
