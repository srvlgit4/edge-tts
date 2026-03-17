import os
import time
import subprocess
import telebot
import asyncio
import edge_tts
import nest_asyncio
import re
import random
from flask import Flask
import threading

# Fix asyncio for multi-threading environments
nest_asyncio.apply()

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = "8712072214:AAEJl5SW1TPisPZb7tiQbYolv-QlDvo_tTU"
VOICE = "hi-IN-MadhurNeural"
RATE = "+30%"
VOLUME = "+20%"

# STEALTH CONFIG: Lowering to 3 lanes is safer for Render's shared IPs
MAX_CONCURRENT_DOWNLOADS = 4 

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ------------------------------------------------------------------
# RENDER "STAY ALIVE" WEB SERVER
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Edge TTS Bot is awake and running!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web_server, daemon=True).start()

print("⚡ Ultimate Stealth Bot with Upload-Retry is LIVE!")

# ------------------------------------------------------------------
# CORE LOGIC
# ------------------------------------------------------------------
def clean_text(text):
    """Protects Hindi characters and removes server-crashing symbols."""
    text = text.replace("\n", " ").replace("अध्याय", "\nअध्याय").replace(",\n", " ")
    # Nuclear Filter: Keep Devanagari, Joiners, and basic punctuation
    text = re.sub(r'[^\w\s\.\,\!\?\"\'।\u200C\u200D\u0900-\u097F]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def split_text_by_length(text, max_chars):
    """Smart sentence chunking to prevent mid-word cuts."""
    sentences = re.split(r'(?<=[।?!.\n])\s+', text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_chars:
            current_chunk += sentence + " "
        else:
            chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

async def tts_chunk(text, filename):
    communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE, volume=VOLUME)
    await communicate.save(filename)

async def process_episode_strict_dealer(chunk_data_list):
    """Parallel downloader with IP-protection delays."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    tasks = []

    async def strict_worker(chunk_txt, chunk_mp3, chunk_idx, total_chunks):
        async with semaphore: 
            for attempt in range(1, 6): 
                try:
                    print(f"   -> 🟢 Processing Chunk {chunk_idx}/{total_chunks} (Attempt {attempt})...")
                    await tts_chunk(chunk_txt, chunk_mp3)
                    return 
                except Exception as e:
                    print(f"   -> ⚠️ Chunk {chunk_idx} failed: {e}. Retrying in 10s...")
                    await asyncio.sleep(10)
            raise Exception(f"❌ Chunk {chunk_idx} failed after 5 attempts.")

    for index, (text, filename, idx, total) in enumerate(chunk_data_list):
        task = asyncio.create_task(strict_worker(text, filename, idx, total))
        tasks.append(task)
        if index < len(chunk_data_list) - 1:
            # Safer randomized delay for long-term stability
            delay = random.randint(10, 15) 
            await asyncio.sleep(delay)

    await asyncio.gather(*tasks)

# ------------------------------------------------------------------
# TELEGRAM HANDLERS
# ------------------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "👋 **Welcome!**\nSend me a `.txt` file containing your novel, and I will convert it to a high-quality Hindi audiobook.")

@bot.message_handler(content_types=['document'])
def handle_novel_upload(message):
    chat_id = message.chat.id
    if not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "❌ Please send a `.txt` file.")
        return

    bot.send_message(chat_id, "📥 Processing file...")
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        raw_text = ""
        for enc in ['utf-8', 'utf-16', 'cp1252']:
            try:
                raw_text = downloaded_file.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        story_text = clean_text(raw_text)
        episodes = split_text_by_length(story_text, max_chars=30000)
        bot.send_message(chat_id, f"📖 Found {len(episodes)} episodes. Starting conversion...")
        
        for ep_idx, episode_text in enumerate(episodes):
            episode_num = ep_idx + 1
            start_time = time.time()
            
            # Network chunks at 1500 chars for Hindi byte-safety
            network_chunks = split_text_by_length(episode_text, max_chars=1500)
            list_file_path = f"list_{episode_num}.txt"
            final_mp3 = f"Episode_{episode_num}.mp3"
            
            chunk_data = []
            chunk_files_to_delete = []
            
            try:
                print(f"\n🎙️ STARTING EPISODE {episode_num}")
                with open(list_file_path, "w", encoding="utf-8") as list_file:
                    for chunk_idx, chunk_txt in enumerate(network_chunks):
                        chunk_mp3 = f"ep_{episode_num}_part_{chunk_idx}.mp3"
                        chunk_data.append((chunk_txt, chunk_mp3, chunk_idx + 1, len(network_chunks)))
                        chunk_files_to_delete.append(chunk_mp3)
                        list_file.write(f"file '{chunk_mp3}'\n")
                
                asyncio.run(process_episode_strict_dealer(chunk_data))
                
                # Glue chunks with FFmpeg
                subprocess.run([
                    "ffmpeg", "-f", "concat", "-safe", "0", 
                    "-i", list_file_path, "-c", "copy", final_mp3, "-y"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                gen_time = round(time.time() - start_time, 1)
                
                # --- UPLOAD WITH RETRY LOOP ---
                print(f"📤 Uploading Episode {episode_num}...")
                for upload_attempt in range(1, 4):
                    try:
                        with open(final_mp3, 'rb') as audio_data:
                            caption = f"🎧 **Episode {episode_num}**\n⚡ Speed: +30%\n⏱️ Generated in {gen_time}s"
                            bot.send_audio(chat_id, audio_data, title=f"Episode {episode_num}", performer="Edge TTS", caption=caption, parse_mode="Markdown")
                            print(f"✅ Episode {episode_num} delivered!")
                            break 
                    except Exception as upload_err:
                        print(f"   ⚠️ Upload failed (Attempt {upload_attempt}): {upload_err}")
                        if upload_attempt == 3:
                            raise Exception("Telegram upload failed after 3 attempts.")
                        time.sleep(5)
                
            except Exception as e:
                bot.send_message(chat_id, f"❌ Error on Episode {episode_num}: {str(e)}")
                break
                
            finally:
                if os.path.exists(final_mp3): os.remove(final_mp3)
                if os.path.exists(list_file_path): os.remove(list_file_path)
                for cf in chunk_files_to_delete:
                    if os.path.exists(cf): os.remove(cf)
                    
        bot.send_message(chat_id, "✅ Conversion Task Finished.")

    except Exception as e:
        bot.reply_to(message, f"❌ Fatal Error: {str(e)}")

bot.infinity_polling()
