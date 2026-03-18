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
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Apply nest_asyncio
nest_asyncio.apply()

# ==========================================
# DEPLOYMENT CONFIGURATION
# ==========================================
# Pull token from Environment Variable (Set this as a Secret in your Hosting Provider)
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN", "8712072214:AAEJl5SW1TPisPZb7tiQbYolv-QlDvo_tTU")
VOICE = "hi-IN-MadhurNeural"
RATE = "+30%"
VOLUME = "+20%"
MAX_CONCURRENT_DOWNLOADS = 5  # Reduced slightly for better stability on shared cloud IPs
CHUNK_SIZE = 2500
EPISODE_SIZE = 35000

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# DUMMY WEB SERVER (Prevents Deployment Sleep)
# ==========================================
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Edge TTS Bot is Online", 200

def run_web_server():
    # Hugging Face and Koyeb look for a response on port 7860 or 8080
    port = int(os.environ.get("PORT", 7860))
    server.run(host='0.0.0.0', port=port)

# Start the server in a background thread
Thread(target=run_web_server, daemon=True).start()

# ==========================================
# CORE LOGIC FUNCTIONS
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
    failed_chunks = 0

    async def strict_worker(chunk_txt, chunk_mp3, chunk_idx, total_chunks):
        nonlocal completed_chunks, failed_chunks
        async with semaphore:
            for attempt in range(1, 6):
                try:
                    # Update progress in Telegram
                    await status_msg.edit_text(
                        f"🎙️ Episode {episode_num}/{total_episodes}\n"
                        f"📦 Chunk {chunk_idx}/{total_chunks} (Attempt {attempt})\n"
                        f"✅ Completed: {completed_chunks}/{total_chunks}\n"
                        f"📝 Preview: {chunk_txt[:30]}..."
                    )
                    
                    success = await tts_chunk(chunk_txt, chunk_mp3)
                    if success:
                        completed_chunks += 1
                        return True
                    else:
                        failed_chunks += 1
                        
                except Exception as e:
                    await asyncio.sleep(10)
            return False

    for index, (text, filename, idx, total) in enumerate(chunk_data_list):
        task = asyncio.create_task(strict_worker(text, filename, idx, total))
        tasks.append(task)
        if index < len(chunk_data_list) - 1:
            await asyncio.sleep(random.randint(8, 13))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [chunk_data_list[i][1] for i, result in enumerate(results) if result is True]

# ==========================================
# TELEGRAM HANDLERS
# ==========================================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        chat_id = update.message.chat_id
        if not doc.file_name.lower().endswith('.txt'): return

        status_msg = await update.message.reply_text("📥 Downloading file...")
        file = await context.bot.get_file(doc.file_id)
        file_content = await asyncio.wait_for(file.download_as_bytearray(), timeout=300)
        
        try: text = file_content.decode('utf-8')
        except: text = file_content.decode('latin-1')
        
        story_text = clean_text(text)
        episodes = split_text_by_length(story_text, EPISODE_SIZE)
        await status_msg.edit_text(f"📖 Found {len(episodes)} episodes. Starting conversion...")

        temp_dir = tempfile.mkdtemp()
        sent_episodes = 0
        
        try:
            for ep_idx, episode_text in enumerate(episodes):
                episode_num = ep_idx + 1
                start_time = time.time()
                network_chunks = split_text_by_length(episode_text, CHUNK_SIZE)
                
                chunk_data = []
                for chunk_idx, chunk_txt in enumerate(network_chunks):
                    chunk_mp3 = os.path.join(temp_dir, f"ep_{episode_num}_part_{chunk_idx}.mp3")
                    chunk_data.append((chunk_txt, chunk_mp3, chunk_idx + 1, len(network_chunks)))
                
                successful_chunks = await process_episode_strict_dealer(chunk_data, status_msg, episode_num, len(episodes))
                
                # Merge
                list_file_path = os.path.join(temp_dir, f"list_{episode_num}.txt")
                final_mp3 = os.path.join(temp_dir, f"Episode_{episode_num}.mp3")
                with open(list_file_path, "w", encoding="utf-8") as list_file:
                    for chunk_file in successful_chunks:
                        list_file.write(f"file '{chunk_file}'\n")
                
                await status_msg.edit_text(f"🔧 Gluing Episode {episode_num}...")
                process = await asyncio.create_subprocess_exec("ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file_path, "-c", "copy", final_mp3, "-y", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(process.communicate(), timeout=300)
                
                # Upload
                if os.path.exists(final_mp3):
                    await status_msg.edit_text(f"📤 Uploading Episode {episode_num}...")
                    with open(final_mp3, 'rb') as audio_data:
                        caption = f"🎧 **Episode {episode_num}**\n⚡ Speed: {RATE}\n⏱️ {round(time.time() - start_time, 1)}s"
                        await context.bot.send_audio(chat_id=chat_id, audio=audio_data, title=f"Episode {episode_num}", performer="Edge TTS", caption=caption, read_timeout=600, write_timeout=600, connect_timeout=600)
                    sent_episodes += 1
                
                # Cleanup episode
                for cf in successful_chunks: 
                    if os.path.exists(cf): os.remove(cf)
                if os.path.exists(list_file_path): os.remove(list_file_path)
                if os.path.exists(final_mp3): os.remove(final_mp3)
                
            await status_msg.edit_text(f"✅ Finished! Sent {sent_episodes} episodes.")

        finally:
            if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).read_timeout(600).write_timeout(600).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot is Ready! Send me a .txt file.")))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    logger.info("🤖 Deployment Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
