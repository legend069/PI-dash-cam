def handle_button_press():
    global locked_ranges
    global button_press_time
    global locked_dir
    global start_locking_thread

    file_wait_time_buffer = max(5, recording_processing_time * 0.5)  # 50% of processing time
    past_time = (button_press_time - timedelta(seconds=record_duration + file_wait_time_buffer)).strftime('%Y%b%d_%H%M%S')
    future_time = (button_press_time + timedelta(seconds=record_duration + file_wait_time_buffer)).strftime('%Y%b%d_%H%M%S')

    if debuglog_button or debuglog_file or debuglog_final_checks:
        log_message(f"Lock button pressed. Locking videos from {past_time} to {future_time}")

    # 🔹 **Lock files as soon as they finish**
    if debuglog_button:
        log_message("Files to be locked:")
    while datetime.now() < button_press_time + timedelta(seconds=record_duration):
        locked_videos = []

        for file_path in list(hash_table.keys()):
            filename = os.path.basename(file_path)
            parts = filename.split('_')

            if len(parts) < 3 or '.' not in parts[2]:
                continue  # Skip files with incorrect format

            timestamp_str = f"{parts[1]}_{parts[2].split('.')[0]}"

            try:
                timestamp_dt = datetime.strptime(timestamp_str, "%Y%b%d_%H%M%S")
            except ValueError:
                continue  # Skip if timestamp is invalid

            if past_time <= timestamp_dt.strftime('%Y%b%d_%H%M%S') <= future_time:
                if file_path not in locked_ranges:
                    new_path = move_to_locked_dir(file_path, locked_dir)
                    locked_ranges.append(new_path)
                    del hash_table[file_path]  # ✅ Remove from hash table after locking
                    locked_videos.append(new_path)

        if locked_videos:
            log_message(f"Locked {len(locked_videos)} files: {', '.join(locked_videos)}")

        time.sleep(1)  # 🔹 Check every 1 second instead of long sleeps

    start_locking_thread = False  # Reset after event

    if debuglog_final_checks:
        log_message("Lock process complete. Waiting for next button press.")




def hash_video(file_path):
    """Compute SHA-256 hash and store metadata in hash_table."""
    if not os.path.exists(file_path):
        log_message(f"Error: File not found for hashing: {file_path}")
        return
    
    hasher = hashlib.sha256()
    file_size = os.path.getsize(file_path)
    created_time = datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%Y-%b-%d %H:%M:%S')
    modified_time = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%b-%d %H:%M:%S')

    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        # 🔹 Store full metadata instead of just the hash
        hash_table[file_path] = {
            "hash": file_hash,
            "size": file_size,
            "created_time": created_time,
            "modified_time": modified_time
        }

        if debuglog_file and debuglog_file_hash:
            log_message(f"File hashed: {file_path}, SHA-256: {file_hash}")

    except Exception as e:
        log_message(f"Error hashing file {file_path}: {e}")
        time.sleep(2)  # Retry delay
        try:
            hash_video(file_path)  # Retry once
        except Exception as e2:
            log_message(f"Retry failed for {file_path}: {e2}")