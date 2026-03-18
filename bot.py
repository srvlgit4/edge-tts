import os
import time
import subprocess
import asyncio
import edge_tts
import nest_asyncio
import re
import random
import tempfile
import shutil
import logging
from pathlib import Path
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Apply nest_asyncio
nest_asyncio.apply()

# ==========================================
# CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = "8712072214:AAEJl5SW1TPisPZb7tiQbYolv-QlDvo_tTU"
VOICE = "hi-IN-MadhurNeural"
RATE = "+30%"
VOLUME = "+20%"
MAX_CONCURRENT_DOWNLOADS = 5  # Safer for Cloud IPs
CHUNK_SIZE = 2500
EPISODE_SIZE = 35000

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# KOYEB HEALTH CHECK SERVER
# ==========================================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Edge TTS Bot is Alive!", 200

def run_flask():
    # Koyeb default port is 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Start Flask in background thread
Thread(target=run_flask, daemon=True).start()

# ==========================================
# CORE FUNCTIONS
# ==========================================
def clean_text(text):
    if not text: return ""
    text = text.replace("\n", " ").replace("अध्याय", "\nअध्याय").replace(",\n", " ")
    text = re.sub(r'[^\w\s\.\,\!\?\"\'।\u200C\u200D\u0900-\u097F\-]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def split_text_by_length(text, max_chars):
    sentences = re.split(r'(?<=[।?!.\n])\s+', text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_chars:
            current_chunk += sentence + " "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    if current_chunk: chunks.append(current_chunk.strip())
    return chunks

def get_progress_bar(completed, total, bar_length=10):
    filled_len = int(round(bar_length * completed / float(total)))
    percents = round(100.0 * completed / float(total), 1)
    bar = '█' * filled_len + '░' * (bar_length - filled_len)
    return f"[{bar}] {percents}%"

async def tts_chunk(text, filename, timeout=120):
    try:
        communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE, volume=VOLUME)
        await asyncio.wait_for(communicate.save(filename), timeout=timeout)
        return True
    except Exception as e:
        logger.warning(f"TTS error: {e}")
        return False

async def process_episode_strict_dealer(chunk_data_list, status_msg, episode_num, total_episodes):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    tasks = []
    completed_chunks = 0
    last_update_time = 0

    async def strict_worker(chunk_txt, chunk_mp3, chunk_idx, total_chunks):
        nonlocal completed_chunks, last_update_time
        async with semaphore:
            for attempt in range(1, 6):
                try:
                    # Update progress every 2 seconds to avoid Telegram Flood Limits
                    if time.time() - last_update_time > 2.0:
                        bar = get_progress_bar(completed_chunks, total_chunks)
                        await status_msg.edit_text(
                            f"🎙️ **Episode {episode_num}/{total_episodes}**\n"
                            f"⚡ Status: Generating Audio\n"
                            f"📊 Progress: {bar}\n"
                            f"📦 Chunk: {chunk_idx}/{total_chunks} (Try {attempt})\n"
                            f"📝 Preview: {chunk_txt[:40]}..."
                        )
                        last_update_time = time.time()

                    success = await tts_chunk(chunk_txt, chunk_mp3)
                    if success:
                        completed_chunks += 1
                        return True
                    await asyncio.sleep(5)
                except Exception as e:
                    await asyncio.sleep(10)
            return False

    for index, (text, filename, idx, total) in enumerate(chunk_data_list):
        task = asyncio.create_task(strict_worker(text, filename, idx, total))
        tasks.append(task)
        if index < len(chunk_data_list) - 1:
            await asyncio.sleep(random.randint(8, 13))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    successful_files = [chunk_data_list[i][1] for i, res in enumerate(results) if res is True]
    return successful_files

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Koyeb Ready!**\nSend me a `.txt` novel file to begin.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        chat_id = update.message.chat_id
        if not doc.file_name.lower().endswith('.txt'): return
        
        status_msg = await update.message.reply_text("📥 Downloading...")
        file = await context.bot.get_file(doc.file_id)
        file_content = await file.download_as_bytearray()
        
        try: text = file_content.decode('utf-8')
        except: text = file_content.decode('latin-1')
        
        story_text = clean_text(text)
        episodes = split_text_by_length(story_text, EPISODE_SIZE)
        await status_msg.edit_text(f"📖 Sliced into {len(episodes)} episodes. Processing...")

        temp_dir = tempfile.mkdtemp()
        sent_episodes = 0
        
        try:
            for ep_idx, episode_text in enumerate(episodes):
                episode_num = ep_idx + 1
                start_time = time.time()
                network_chunks = split_text_by_length(episode_text, CHUNK_SIZE)
                
                chunk_data = []
                for idx, txt in enumerate(network_chunks):
                    path = os.path.join(temp_dir, f"ep_{episode_num}_p{idx}.mp3")
                    chunk_data.append((txt, path, idx + 1, len(network_chunks)))
                
                successful_chunks = await process_episode_strict_dealer(chunk_data, status_msg, episode_num, len(episodes))
                
                # Glue
                list_path = os.path.join(temp_dir, f"list_{episode_num}.txt")
                final_mp3 = os.path.join(temp_dir, f"Ep_{episode_num}.mp3")
                with open(list_path, "w", encoding="utf-8") as f:
                    for cf in successful_chunks: f.write(f"file '{cf}'\n")
                
                await status_msg.edit_text(f"🔧 Gluing Episode {episode_num}...")
                proc = await asyncio.create_subprocess_exec("ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", final_mp3, "-y", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await proc.communicate()
                
                # Upload
                if os.path.exists(final_mp3):
                    await status_msg.edit_text(f"📤 Uploading Episode {episode_num}...")
                    with open(final_mp3, 'rb') as audio:
                        caption = f"🎧 **Episode {episode_num}**\n⏱️ Time: {round(time.time()-start_time, 1)}s"
                        await context.bot.send_audio(chat_id=chat_id, audio=audio, title=f"Episode {episode_num}", performer="Edge TTS", caption=caption, read_timeout=600, write_timeout=600)
                    sent_episodes += 1

                # Episode Cleanup
                for cf in successful_chunks: 
                    if os.path.exists(cf): os.remove(cf)
                if os.path.exists(list_path): os.remove(list_path)
                if os.path.exists(final_mp3): os.remove(final_mp3)
                
            await status_msg.edit_text(f"✅ Finished! Total episodes: {sent_episodes}")

        finally:
            if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
            
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

def main():
    app_bot = Application.builder().token(TELEGRAM_BOT_TOKEN).read_timeout(600).write_timeout(600).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app_bot.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
