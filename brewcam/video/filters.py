def build_compose_command(display_source, rtsp_url, overlay_text_path, stream_config, snapshot_path):
    width, height, fps = stream_config["width"], stream_config["height"], stream_config["fps"]
    mode = display_source["mode"]
    command = ["ffmpeg"]
    if mode == "camera":
        command += ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", rtsp_url]
    elif mode == "image":
        command += ["-loop", "1", "-framerate", str(fps), "-i", display_source["path"]]
    else:
        command += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]
    filter_chain = (
        f"scale={width}:{height},"
        f"drawtext=textfile={overlay_text_path}:reload=1:fontcolor=white:fontsize=24:"
        "x=20:y=20:box=1:boxcolor=black@0.5"
    )
    command += ["-vf", filter_chain, "-an", "-pix_fmt", "yuv420p", "-r", "1/5", "-update", "1", "-y", str(snapshot_path)]
    return command
