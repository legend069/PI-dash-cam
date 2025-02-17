import RPi.GPIO as GPIO
import time
import os
import hashlib
import threading
import psutil
import csv
from datetime import datetime
import sys
import select
from datetime import timedelta

LED_RECORDING = 15
LED_TRANSFER = 2
LED_BUTTON_PRESS_EVENT = 3
LED_ERROR = 4
BUTTON_LOCK = 14

recording_state = False
record_duration = 300 #seconds
record_video_creation_time = datetime.now()
video_days_to_keep = 14 # keep min last 14 days of files.
recording_processing_time = 0  # Global variable to store processing time
locked_ranges = []
lock_duration = record_duration * 3 # Lock videos recorded within this time range (in seconds)
hash_table = {}
hash_table_max_size = 20  # Keep only the most recent 20 entries
video_dir = "/home/wrx/videos/"
log_file = "/home/wrx/videos/log.txt"
locked_dir = os.path.join(video_dir, "locked")
csv_log_file = "/home/wrx/videos/locked_videos.csv"
threshold = 10 #gb
button_press_time = datetime.now()
button_press_sim = False
lock_in_progress = None
error_led = False


##debug options
debuglog_all = False
debuglog_none = False

monitor_cpu = False
debuglog_LED_state = True
debuglog_recording = False
debuglog_errors = True
debuglog_file = False
debuglog_button = False
debuglog_file_hash = False
debuglog_final_checks = True ##used for basic debuggging assuming everything else runs ok.

if debuglog_all:
    debuglog_LED_state = True
    debuglog_recording = True
    debuglog_errors = True
    debuglog_file = True
    debuglog_button = True
    debuglog_file_hash = True
    debuglog_final_checks = True
if debuglog_none:
    debuglog_LED_state = False
    debuglog_recording = False
    debuglog_errors = True
    debuglog_file = False
    debuglog_button = False
    debuglog_file_hash = False
    debuglog_final_checks = False

def is_console_open():
    return sys.stdout.isatty() or os.getenv("SSH_CLIENT") is not None


def log_message(message, color="white"):
    rotate_log()  # Rotate before logging

    timestamp = datetime.now().strftime('%Y-%b-%d %H:%M:%S')
    # Determine color based on message content
    if "error" in message.lower():
        color = "red"
    elif "warning" in message.lower():
        color = "yellow"
    elif color == "green":
        color = "green"
    else:
        color = "white"

    color_codes = {
        "green": "\033[92m",
        "red": "\033[91m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "white": "\033[97m",
        "reset": "\033[0m"
    }

    # Apply the selected color if running in a terminal
    if is_console_open() and color in color_codes:
        message = f"{color_codes[color]}{timestamp} - {message}{color_codes['reset']}"
        print(message)

    # Log without color to file
    log_entry = f"{timestamp} - {message.replace(color_codes[color], '').replace(color_codes['reset'], '')}"
    with open(log_file, "a") as log:
        log.write(log_entry + "\n")



def toggle_recording(state):
    global recording_state
    recording_state = state

    if debuglog_LED_state:
        log_message(f"Recording LED {'on' if state else 'off'}")
    GPIO.output(LED_RECORDING, GPIO.LOW if state else GPIO.HIGH)
    if debuglog_recording:
        log_message(f"Recording {'started' if state else 'stopped'}")


def time_recording_event():
    global recording_processing_time

    if not check_disk_space():
        toggle_recording(False)
        return
    
    start_time = time.time()  # Start timing
    record_video()
    end_time = time.time()
    
    recording_processing_time = end_time - start_time - record_duration
    if debuglog_recording or debuglog_button or debuglog_final_checks:
        log_message(f"Recording process took {record_duration:.2f} + {recording_processing_time:.2f} seconds ")


def check_disk_space():
    """Checks available disk space and auto-cleans if low."""
    global threshold  # GB
    free_space_mb = (os.statvfs(video_dir).f_bavail * os.statvfs(video_dir).f_frsize) / (1024 * 1024)
    
    if free_space_mb < threshold * 1024:  # Convert GB to MB
        if debuglog_errors or debuglog_file:
            log_message(f"Error: Low disk space ({free_space_mb:.2f}MB left). Attempting cleanup...")
        auto_cleanup()
        return check_disk_space()  # Re-check space after cleanup
    return True


def move_to_locked_dir(file_path, locked_dir):
    """Move file to locked directory, preventing overwriting"""
    
    base_name = os.path.basename(file_path)
    new_path = os.path.join(locked_dir, base_name)

    counter = 1
    while os.path.exists(new_path):
        name, ext = os.path.splitext(base_name)
        new_path = os.path.join(locked_dir, f"{name}_{counter}{ext}")
        counter += 1
        if counter > 99: # Hard limit
            if debuglog_errors:
                log_message(f"Warning: Too many duplicate files for {base_name}. Using timestamp instead.")
            timestamp = datetime.now().strftime("%Y%b%d_%H%M%S")
            new_path = os.path.join(locked_dir, f"{name}_{timestamp}{ext}")
            break  

    if os.path.exists(file_path):  
        #if file_path in hash_table:
        os.rename(file_path, new_path)
        threading.Thread(target=save_locked_video_info, args=(new_path, button_press_time), daemon=True).start()

    elif os.path.exists(new_path):  
        if debuglog_errors or debuglog_file or debuglog_button:
            log_message(f"ERROR: {new_path} already exists creating rollover")
            
        rollover_counter = 1
        rollover_path = os.path.join(locked_dir, f"rollover_{base_name}")

        while os.path.exists(rollover_path):
            rollover_path = os.path.join(locked_dir, f"rollover_{rollover_counter}_{base_name}")
            rollover_counter += 1

        os.rename(new_path, rollover_path)
        if debuglog_errors or debuglog_file or debuglog_button:
            log_message(f"Rollover file created: {rollover_path}")

    return new_path


def record_video():
    global record_video_creation_time

    toggle_recording(True)
    timestamp = datetime.now().strftime("_%Y%b%d_%H%M%S")
    file_path = os.path.join(video_dir, f"video{timestamp}.mp4")
    if debuglog_recording:
        log_message(f"timestamp: {timestamp}")
        log_message(f"file path: {file_path}")
        log_message(f"record duration: {record_duration}")
    record_video_creation_time = datetime.now().strftime("%Y-%b-%d %H:%M:%S")
    temp = record_video_creation_time
    success = os.system(f"libcamera-vid -o {file_path} -t {record_duration}sec -v 0 --nopreview --vflip 1 --hflip 1 --width 2304 --height 1296 --framerate 35 --codec h264 > /dev/null 2>&1")
    record_video_creation_time = temp

    if os.path.exists(file_path) and success == 0:
        hash_video(file_path)
    else:
        if debuglog_file or debuglog_recording or debuglog_errors:
            log_message(f"Error: Recording failed, file not created: {file_path}")
        
        if debuglog_recording or debuglog_errors:
            log_message("Retrying recording in 2 seconds...")
        time.sleep(2)
        
        # Retry once
        success = os.system(f"libcamera-vid -o {file_path} -t {record_duration}sec -v 0 --nopreview --vflip 1 --hflip 1 --width 2304 --height 1296 --framerate 35 --codec h264 > /dev/null 2>&1")
        
        if os.path.exists(file_path) and success == 0:
            hash_video(file_path)
        else:
            if debuglog_errors or debuglog_file:
                log_message(f"Critical Error: Second recording attempt failed for {file_path}")
    
    if not os.path.exists(file_path) or success != 0:
        if debuglog_errors:
            log_message(f"Error: Recording failed after 2 attempts: {file_path}")
        toggle_recording(False)
        return  # Ensures no further execution


def hash_video(file_path):
    """Compute SHA-256 hash and store metadata in hash_table."""
    if not os.path.exists(file_path):
        log_message(f"Error: File not found for hashing: {file_path}")
        return
    
    hasher = hashlib.sha256()
    file_size = os.path.getsize(file_path)
    created_time = record_video_creation_time
    #created_time = datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%Y-%b-%d %H:%M:%S')#returns timestamp when the file is finished. could extrat timestamp from file name too...
    finished_time = datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%Y-%b-%d %H:%M:%S') # or datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%b-%d %H:%M:%S')
    file_locked = False

    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        file_hash = hasher.hexdigest()

        if debuglog_file and debuglog_file_hash:
            log_message(f"File hashed: {file_path}, SHA-256: {file_hash}")
        
        # Check if the file is inside the locked directory
        if file_path.startswith(locked_dir):
            original_filename = os.path.basename(file_path)
            unlocked_path = os.path.join(video_dir, original_filename)

            # **Check if the hash matches the unlocked file**
            if unlocked_path in hash_table and hash_table[unlocked_path]["hash"] == file_hash:
                file_locked = True
                hash_table[unlocked_path]["file_locked"] = True
                if debuglog_file and debuglog_file_hash:
                    log_message(f"Updated hash_table: Marked {unlocked_path} as locked.")
            else:
                log_message(f"Warning: Locked file {file_path} not found in hash_table or hash mismatch.")

            return file_hash  # Return computed hash

        # **Store metadata if the file is not yet locked**
        hash_table[file_path] = {
            "hash": file_hash,
            "size": file_size,
            "created_time": created_time,
            "finished_time": finished_time,
            "file_locked": file_locked
        }

    except Exception as e:
        log_message(f"Error hashing file {file_path}: {e}")
        time.sleep(2)  # Retry delay
        try:
            hash_video(file_path)  # Retry once
        except Exception as e2:
            log_message(f"Retry failed for {file_path}: {e2}")
            return None


def auto_cleanup_check_only():
    """Displays the list of videos sorted by creation time and checks which files will be deleted."""
    files = sorted(
        [os.path.join(video_dir, f) for f in os.listdir(video_dir) if f.startswith("video_")],
        key=os.path.getmtime
        #key=os.path.getctime

        # getmtime = last modifed time
        # getctime = creation time
    )
    if debuglog_file or debuglog_final_checks:
        log_message("Files sorted by oldest first (potential deletions):")
    now = time.time()
    deletable_files = []

    for file in files:
        creation_time = datetime.fromtimestamp(os.path.getmtime(file)).strftime('%Y-%b-%d %H:%M:%S')
        file_age_days = (now - os.path.getmtime(file)) / (60 * 60 * 24)
        #creation_time = datetime.getctime(os.path.getmtime(file)).strftime('%Y-%b-%d %H:%M:%S')
        #file_age_days = (now - os.path.getctime(file)) / (60 * 60 * 24)

        if debuglog_file or debuglog_final_checks or debuglog_errors:
            log_message(f"{file} - Created: {creation_time} ({file_age_days:.2f} days ago)")
        
        if file_age_days > video_days_to_keep:
            deletable_files.append(file)
    if debuglog_file or debuglog_final_checks:
        log_message(f"Total files eligible for deletion: {len(deletable_files)}")


def auto_cleanup():
    """Deletes the oldest unlocked videos based on storage threshold and retention period."""
    global video_days_to_keep
    start_time = time.time()

    # Check storage space
    free_space_mb = (os.statvfs(video_dir).f_bavail * os.statvfs(video_dir).f_frsize) / (1024 * 1024)
    if free_space_mb > threshold * 1024:  # If storage is not over the threshold, exit
        if debuglog_file or debuglog_final_checks:
            log_message(f"Storage check: {free_space_mb:.2f}MB available. No cleanup needed.", color="green")
        return

    files = sorted(
        [os.path.join(video_dir, f) for f in os.listdir(video_dir) if f.startswith("video_")],
        key=os.path.getctime  # Use creation time to determine age
    )

    now = time.time()
    days_to_keep = video_days_to_keep
    files_to_delete = []

    # Reduce days_to_keep dynamically until deletable files are found
    while not files_to_delete and days_to_keep >= 0:
        files_to_delete = [f for f in files if (now - os.path.getctime(f)) / (60 * 60 * 24) > days_to_keep]
        if not files_to_delete:
            days_to_keep -= 1  # Decrease retention period

    # If still no deletable files, force delete the 5 oldest videos
    if not files_to_delete:
        if len(files) <= 5:
            if debuglog_file or debuglog_errors:
                log_message("Error: No deletable files found. Cannot proceed with auto-cleanup.")
            return
        if debuglog_file or debuglog_errors:
            log_message("Warning: No old videos found within retention period. Deleting the 5 oldest videos.")
        files_to_delete = files[:5]

    # Proceed with deletion
    if files_to_delete:
        if debuglog_file or debuglog_final_checks:
            log_message(f"Deleting {len(files_to_delete)} old videos:\n" + "\n".join(files_to_delete))
        for file in files_to_delete:
            try:
                os.remove(file)
                if debuglog_file:
                    log_message(f"Deleted old video: {file}")
            except Exception as e:
                if debuglog_file or debuglog_errors:
                    log_message(f"Error deleting {file}: {e}")
    end_time = time.time()

    delete_processing_time = end_time - start_time
    if debuglog_recording or debuglog_button or debuglog_final_checks:
        log_message(f"deletion process took {delete_processing_time:.2f} seconds")


def rotate_csv():
    """Renames the old CSV file instead of deleting data when it exceeds max size."""
    max_lines = 5000  # Set a limit for CSV file size
    try:
        with open(csv_log_file, 'r') as file:
            lines = file.readlines()

        if len(lines) > max_lines:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_csv_file = f"/home/wrx/videos/locked_videos_{timestamp}.csv"

            os.rename(csv_log_file, archive_csv_file)
            if debuglog_errors or debuglog_file:
                log_message(f"CSV rotated: {archive_csv_file}")
            
            # Create a new CSV file with a header
            with open(csv_log_file, 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(["Filename", "Hash", "Button Press Timestamp"])

    except FileNotFoundError:
        pass  # If the file doesn't exist, no need to rotate


def rotate_log():
    """Rotates the log file when it exceeds a max size."""
    max_log_size = 20 * 1024 * 1024  # 20MB limit

    try:
        if os.path.exists(log_file) and os.path.getsize(log_file) > max_log_size:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_log_file = f"/home/wrx/videos/log_{timestamp}.txt"

            os.rename(log_file, archive_log_file)
            if debuglog_errors or debuglog_file:
                log_message(f"Log rotated: {archive_log_file}")

            # Create a new log file
            with open(log_file, "w") as file:
                file.write(f"{datetime.now().strftime('%Y-%b-%d %H:%M:%S')} - Log rotation initialized.\n")

    except Exception as e:
        log_message(f"Error rotating log file: {e}")


def save_locked_video_info(locked_file_path, button_press_time):
    """Logs the locked video information to a CSV file."""
    if debuglog_button or debuglog_file or debuglog_final_checks:
        log_message(f"Creating CSV entry for locked file: {locked_file_path}")

    original_filename = os.path.basename(locked_file_path)
    unlocked_path = os.path.join(video_dir, original_filename)

    # **Check if the file exists in hash_table and is marked as locked**
    #if unlocked_path in hash_table and hash_table[unlocked_path]["file_locked"]:# hash_table[unlocked_path]["file_locked"] will never work because the locked flag gets set when 'hash_video' gets called with a locked path.
    if unlocked_path in hash_table: 
        #file_hash = hash_table[unlocked_path]["hash"]
        file_hash = hash_video(locked_file_path) #
    else:
        if debuglog_errors or debuglog_file:
            log_message(f"Error: Hash missing for {locked_file_path}. Skipping CSV entry.")
        return

    file_exists = os.path.isfile(csv_log_file)
    with open(csv_log_file, mode='a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Filename", "Hash", "Button Press Timestamp"])
        writer.writerow([locked_file_path, file_hash, button_press_time.strftime('%Y-%b-%d %H:%M:%S')])

    rotate_csv()


def handle_button_press():
    global locked_ranges
    global button_press_time
    global locked_dir
    global lock_in_progress

    while not lock_in_progress:
        time.sleep(0.05)  # Prevent CPU overuse while waiting for button press
    
    wait_time = 0

    while len(hash_table) == 0:
        if debuglog_button or debuglog_file or debuglog_final_checks:
            log_message(f"Waiting for first real video to populate hash_table... ({wait_time} sec)")
        time.sleep(1)
        wait_time += 1
        if wait_time > record_duration + 5:  # Safety timeout
            log_message("Warning: No valid video found in hash_table after waiting. Skipping lock.")
            lock_in_progress = False
            return

    if len(hash_table) == 1:# hash_table is just created, give it a little more time to start next recording.
        log_message(f"hash_table size :{len(hash_table)} waiting 5 seconds for next vid to start")
        time.sleep(5)

    if not hash_table:
        if debuglog_button or debuglog_file or debuglog_final_checks:
            log_message("Warning: No videos found in hash_table after waiting. Skipping lock.")
        lock_in_progress = False
        return

    if debuglog_button or debuglog_file or debuglog_final_checks:
        log_message("Lock button pressed. Preparing to lock video files.", color="green")

    hash_table_index = list(hash_table.keys())
    hash_table_index.sort(key=lambda x: datetime.strptime(hash_table[x]["finished_time"], "%Y-%b-%d %H:%M:%S"))

    # Step 1: Identify the past video by checking creation timestamps closest to button_press_time
    past_video = None
    closest_time_diff = float('inf')
    for i in range(len(hash_table_index) - 1, -1, -1):
        
        video_time = datetime.strptime(hash_table[hash_table_index[i]]["created_time"], "%Y-%b-%d %H:%M:%S")
        if video_time <= button_press_time:
            time_diff = (button_press_time - video_time).total_seconds()

            if time_diff < closest_time_diff:
                closest_time_diff = time_diff
                past_video = hash_table_index[i]
            if len(hash_table_index) == 1:
                past_video = hash_table_index[0]
            else:
                break  # Exit early once we go too far
    
    if past_video is None:
        if debuglog_errors:
            log_message("warning: No valid past video found. selecting first entry")
        lock_in_progress = False
        if len(hash_table) > 0:
            past_video = hash_table_index[0]
        else:
            return

    past_video_time = datetime.strptime(hash_table[past_video]["created_time"], "%Y-%b-%d %H:%M:%S")
    if past_video and not hash_table[past_video]["file_locked"]:
        new_path = move_to_locked_dir(past_video, locked_dir)
        locked_ranges.append(new_path)
        if debuglog_button or debuglog_file or debuglog_final_checks:
            log_message(f"Locked past video: {past_video}", color ="green")
    else:
        if debuglog_button or debuglog_file or debuglog_final_checks:
            log_message(f"warning: Skipping already locked past video: {past_video}")

    # **Determine when to lock the current video**###############################################################################################
    current_elapsed_time = (datetime.now() - button_press_time).total_seconds()
    current_time = past_video_time + timedelta(seconds=record_duration + (recording_processing_time*4))
    display_current_time = past_video_time + timedelta(seconds=record_duration)
    display_current_end_time = display_current_time + timedelta(seconds=record_duration + recording_processing_time)
    log_message(f"Current video expected at: {display_current_time.strftime('%Y-%b-%d %H:%M:%S')} and should end at: {display_current_end_time.strftime('%Y-%b-%d %H:%M:%S')}", color="green")
    # **Wait for the current video to finish recording**
    current_timeout_time = current_time + timedelta(seconds=record_duration) # record_duration sec buffer
    while datetime.now() < current_time:
        if datetime.now() > current_timeout_time:
            if debuglog_errors:
                log_message("error: Current video never recorded.", color="red")
            break
        time.sleep(0.5)

    current_hash_size = len(hash_table)
    while len(hash_table) == current_hash_size:
        time.sleep(0.5)  # Check every 0.5 seconds

    hash_table_index = list(hash_table.keys())
    hash_table_index.sort(key=lambda x: datetime.strptime(hash_table[x]["finished_time"], "%Y-%b-%d %H:%M:%S"))

    # Step 2: Identify the current video
    current_video = None
    for i in hash_table_index:
        video_time = datetime.strptime(hash_table[i]["created_time"], "%Y-%b-%d %H:%M:%S")
        if video_time > past_video_time:
            if debuglog_button and debuglog_file:
                log_message(f"found current video: {i}" , color ="green")
            current_video = i
            break  # Stop at the first video after past_video

    if not current_video:
        log_message("ERROR: current video not found")    

    current_video_time = datetime.strptime(hash_table[current_video]["created_time"], "%Y-%b-%d %H:%M:%S")
    if current_video:
        if current_video and not hash_table[current_video]["file_locked"]:
            new_path = move_to_locked_dir(current_video, locked_dir)
            locked_ranges.append(new_path)
            if debuglog_button or debuglog_file or debuglog_final_checks:
                log_message(f"Locked current video: {current_video}", color ="green")
        else:
            if debuglog_button or debuglog_file or debuglog_final_checks:
                log_message(f"warning: Skipping already locked current video: {current_video}")
    else:
        if debuglog_errors:
            log_message(f"error: fatal with handle_button_press, missing current_video{current_video}")

    # **Determine when to lock the future video**###############################################################################################
    elapsed_time = (datetime.now() - button_press_time).total_seconds()
    future_time_offset = record_duration - elapsed_time  # Time left in the current video
    future_time = current_video_time + timedelta(seconds=record_duration + (recording_processing_time*4))
    display_future_time = current_video_time + timedelta(seconds=record_duration)
    display_future_end_time = display_future_time + timedelta(seconds=record_duration + recording_processing_time)
    log_message(f"Future video expected at: {display_future_time.strftime('%Y-%b-%d %H:%M:%S')} and should end at: {display_future_end_time.strftime('%Y-%b-%d %H:%M:%S')}", color="green")

    future_timeout_time = future_time + timedelta(seconds=record_duration)  # record_duration sec buffer
    while datetime.now() < future_time:
        if datetime.now() > future_timeout_time:
            if debuglog_errors:
                log_message("error: Future video never recorded.", color="red")
            break
        time.sleep(0.5)
    
    future_hash_size = len(hash_table)
    while len(hash_table) == future_hash_size:
        time.sleep(0.5)  # Wait until future video appears in hash_table
        
    hash_table_index = list(hash_table.keys())
    hash_table_index.sort(key=lambda x: datetime.strptime(hash_table[x]["finished_time"], "%Y-%b-%d %H:%M:%S"))

    # Step 3: Identify the future video
    future_video = None
    for i in hash_table_index:
        video_time = datetime.strptime(hash_table[i]["created_time"], "%Y-%b-%d %H:%M:%S")
        if video_time > current_video_time:
            if debuglog_button and debuglog_file:
                log_message(f"found future video: {i}" , color ="green")
            future_video = i
            break  # Stop at the first video after past_video
    if future_video:
        if future_video and not hash_table[future_video]["file_locked"]:
            new_path = move_to_locked_dir(future_video, locked_dir)
            locked_ranges.append(new_path)
            if debuglog_button or debuglog_file or debuglog_final_checks:
                log_message(f"Locked future video: {future_video}", color="green")
        else:
            if debuglog_button or debuglog_file or debuglog_final_checks:
                log_message(f"Skipping already locked future video: {future_video}")
    else:
        if debuglog_errors:
            log_message(f"error: fatal with handle_button_press, missing current_video{current_video}")

    if debuglog_button or debuglog_file or debuglog_final_checks:
        log_message("Lock process complete. Waiting for next button press.", color="green")
    # i should prob do something with 'locked_ranges'
    lock_in_progress = False  # Reset flag for the next button press


def monitor_ssh_input():
    """ Monitors SSH keyboard inputs for manual control. """
    global button_press_time
    global button_press_sim
    global lock_in_progress

    lock = threading.Lock()

    while True:
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1).strip().lower()

            if key == "l":  # Simulate button press
                with lock:
                    if debuglog_button or debuglog_final_checks:
                        log_message("SSH Key Press: Simulating button lock press.")
                    if not lock_in_progress:
                        button_press_time = datetime.now()
                        button_press_sim = True
                        lock_in_progress = True

            elif key == "q":  # Quit program
                with lock:
                    if debuglog_button or debuglog_final_checks:
                        log_message("SSH Key Press: Exiting...")
                    cleanup()
                    sys.exit()
            else:
                time.sleep(0.1)
                with lock:
                    key = None
        time.sleep(0.1)


def file_transfer():
    GPIO.output(LED_TRANSFER, GPIO.HIGH)
    if debuglog_file:
        log_message("Starting file transfer...")
    time.sleep(1)
    GPIO.output(LED_TRANSFER, GPIO.LOW)
    if debuglog_file:
        log_message("File transfer complete.")


def setup():
    GPIO.setmode(GPIO.BCM) # use PHYSICAL GPIO Numbering
    os.environ["LIBCAMERA_LOG_LEVELS"] = "0"

    GPIO.setup(LED_RECORDING, GPIO.OUT)
    GPIO.setup(LED_TRANSFER, GPIO.OUT)
    GPIO.setup(LED_BUTTON_PRESS_EVENT, GPIO.OUT)
    GPIO.setup(BUTTON_LOCK, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    if not os.path.exists(video_dir):
        os.makedirs(video_dir)

    if not os.path.exists(locked_dir):
        os.makedirs(locked_dir)
    
    #auto_cleanup_check_only()
    auto_cleanup()
    rotate_csv()

    if debuglog_all:
        log_message("Setup completed")



def cleanup():
    GPIO.output(LED_RECORDING, GPIO.LOW) #low set to GND
    GPIO.output(LED_TRANSFER, GPIO.LOW)  #high set to 3.3
    GPIO.output(LED_BUTTON_PRESS_EVENT,GPIO.LOW)
    GPIO.cleanup()
    if debuglog_all:
        log_message("GPIO cleaned up.")


def button_state_led():
    global button_press_time
    global lock_in_progress

    lock = threading.Lock()

    previous_state = GPIO.input(BUTTON_LOCK)
    while True:
        state = GPIO.input(BUTTON_LOCK)
        if state != previous_state:    
            if debuglog_LED_state or debuglog_button:
                log_message(f"Button LED {'off' if state else 'on'}")
            GPIO.output(LED_BUTTON_PRESS_EVENT, GPIO.HIGH if state else GPIO.LOW)
            previous_state = state
            time.sleep(1)  # Button stays on for 1 second
            if not lock_in_progress:
                with lock:
                    button_press_time = datetime.now()
                    lock_in_progress = True
        else:
            #if debuglog_button or debuglog_final_checks:
            #    log_message("Button press ignored. Locking already in progress.")
            time.sleep(0.05)


def error_state_led():
    """Turns on the error LED when an error occurs."""
    GPIO.output(LED_ERROR, GPIO.HIGH)
    if debuglog_LED_state or debuglog_errors:
        log_message(f"Error LED ON")
    time.sleep(0.1)
    GPIO.output(LED_ERROR, GPIO.LOW)
    if debuglog_LED_state or debuglog_errors:
        log_message(f"Error LED OFF")


def monitor_cpu_usage():
    while True:
        cpu_usage = psutil.cpu_percent(interval=2)
        if cpu_usage > 20:  # Only log if usage is above 20%
            log_message(f"High CPU Usage: {cpu_usage}%")
        time.sleep(2)


def monitor_errors():
    """Monitors for errors and flashes the error LED."""
    global error_led

    while True:
        if error_led:
            error_state_led()
            time.sleep(0.5)  # error led blink rate
        else:
            time.sleep(2)  # Lower CPU usage when idle


if __name__ == "__main__":
    setup()
    last_recording_time = datetime.now()
    cpu_monitor = threading.Thread(target=monitor_cpu_usage, daemon=True)
    if monitor_cpu:
        log_message("monitoring CPU")
        cpu_monitor.start()

    recording_thread = threading.Thread(target=time_recording_event, daemon=True)
    recording_thread.start()
    button_pressed_event = threading.Thread(target=handle_button_press, daemon=True)
    button_pressed_event.start()

    ssh_input_thread = threading.Thread(target=monitor_ssh_input, daemon=True)
    ssh_input_thread.start()
    led_button_press = threading.Thread(target=button_state_led, daemon=True)
    led_button_press.start()

    time.sleep(2)
    try:
        idle_counter = 0
        while True:
            time.sleep(0.5)

            if not ssh_input_thread.is_alive():
                if debuglog_all:
                    log_message("ssh thread is dead, starting thread")
                ssh_input_thread = threading.Thread(target=monitor_ssh_input, daemon=True)
                ssh_input_thread.start()
            if not led_button_press.is_alive():
              if debuglog_all:
                  log_message("button thread is dead, starting thread")
              led_button_press = threading.Thread(target=button_state_led, daemon=True)
              led_button_press.start()
            if not button_pressed_event.is_alive():
                if debuglog_all:
                    log_message("button thread is dead, starting thread")
                button_pressed_event = threading.Thread(target=handle_button_press, daemon=True)
                button_pressed_event.start()
              
            if not recording_thread.is_alive():
                elapsed_time = (datetime.now() - last_recording_time).total_seconds()
                if elapsed_time > record_duration + 0.1:
                    if debuglog_all:
                        log_message("recording is dead, starting thread")
                    recording_thread = threading.Thread(target=time_recording_event, daemon=True)
                    recording_thread.start()
                    last_recording_time = datetime.now()  # <-- Update time
            
            if len(hash_table) > hash_table_max_size:
                oldest_keys = list(hash_table.keys())[:5]  # Delete the 5 oldest entries
                for key in oldest_keys:
                    del hash_table[key]

            idle_counter += 1
            if idle_counter % 10 == 0:
                if debuglog_all:
                    log_message("idle")

    except Exception as e:
        if debuglog_errors:
            log_message(f"Critical Error in main loop: {e}")
        cleanup()

    except KeyboardInterrupt:
        cleanup()
