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

# Fix asyncio for multi-threading
nest_asyncio.apply()

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
# Note: On Render, it's safer to use Environment Variables for tokens, 
# but I left your token here so it works instantly on copy-paste.
TELEGRAM_BOT_TOKEN = "8712072214:AAEJl5SW1TPisPZb7tiQbYolv-QlDvo_tTU"
VOICE = "hi-IN-MadhurNeural"
RATE = "+30%"
VOLUME = "+20%"
MAX_CONCURRENT_DOWNLOADS = 7

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ------------------------------------------------------------------
# RENDER "STAY ALIVE" WEB SERVER
# ------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Edge TTS Bot is awake and running!"

def run_web_server():
    # Render assigns a dynamic port. We catch it here.
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Start the Flask server in a background thread
threading.Thread(target=run_web_server, daemon=True).start()

print("⚡ Ultimate 5-Lane Stealth Bot is LIVE! Waiting for your text file...")

# ------------------------------------------------------------------
# CORE LOGIC
# ------------------------------------------------------------------
def clean_text(text):
    text = text.replace("\n", " ").replace("अध्याय", "\nअध्याय").replace(",\n", " ")
    text = re.sub(r'[^\w\s\.\,\!\?\"\'।\u200C\u200D\u0900-\u097F]', ' ', text)
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
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

async def tts_chunk(text, filename):
    communicate = edge_tts.Communicate(text=text, voice=VOICE, rate=RATE, volume=VOLUME)
    await communicate.save(filename)

async def process_episode_strict_dealer(chunk_data_list):
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
            raise Exception(f"❌ Chunk {chunk_idx} completely failed after 5 attempts.")

    for index, (text, filename, idx, total) in enumerate(chunk_data_list):
        task = asyncio.create_task(strict_worker(text, filename, idx, total))
        tasks.append(task)
        if index < len(chunk_data_list) - 1:
            delay = random.randint(8, 13) 
            print(f"   ⏳ Dealer waiting {delay}s before dispatching Chunk {idx + 1}...")
            await asyncio.sleep(delay)

    await asyncio.gather(*tasks)

# ------------------------------------------------------------------
# TELEGRAM HANDLER
# ------------------------------------------------------------------
@bot.message_handler(content_types=['document'])
def handle_novel_upload(message):
    chat_id = message.chat.id
    if not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "❌ Please send a `.txt` file.")
        return

    bot.send_message(chat_id, "📥 Downloading your novel...")
    
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
        bot.send_message(chat_id, f"🔪 Book sliced into {len(episodes)} episodes.\n🚀 Starting Ultimate 7-Lane Generation...")
        
        for ep_idx, episode_text in enumerate(episodes):
            episode_num = ep_idx + 1
            start_time = time.time()
            
            network_chunks = split_text_by_length(episode_text, max_chars=1500)
            list_file_path = f"list_{episode_num}.txt"
            final_mp3 = f"Episode_{episode_num}.mp3"
            
            chunk_data = []
            chunk_files_to_delete = []
            
            try:
                print(f"\n" + "="*50)
                print(f"🎙️ LOCKING EPISODE {episode_num}")
                print(f"="*50)
                
                with open(list_file_path, "w", encoding="utf-8") as list_file:
                    for chunk_idx, chunk_txt in enumerate(network_chunks):
                        chunk_mp3 = f"ep_{episode_num}_part_{chunk_idx}.mp3"
                        chunk_data.append((chunk_txt, chunk_mp3, chunk_idx + 1, len(network_chunks)))
                        chunk_files_to_delete.append(chunk_mp3)
                        list_file.write(f"file '{chunk_mp3}'\n")
                
                asyncio.run(process_episode_strict_dealer(chunk_data))
                
                print(f"\n📦 All chunks secured. Gluing Episode {episode_num}...")
                subprocess.run([
                    "ffmpeg", "-f", "concat", "-safe", "0", 
                    "-i", list_file_path, 
                    "-c", "copy", final_mp3, "-y"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                generation_time = round(time.time() - start_time, 1)
                
                print(f"📤 Uploading Episode {episode_num} to Telegram...")
                with open(final_mp3, 'rb') as audio_data:
                    caption = f"🎧 **Episode {episode_num}**\n⚡ Speed: +30%\n⏱️ Generated in {generation_time}s"
                    bot.send_audio(chat_id, audio_data, title=f"Episode {episode_num}", performer="Edge TTS", caption=caption, parse_mode="Markdown")
                    print(f"✅ Episode {episode_num} successfully delivered!")
                    
            except Exception as e:
                error_msg = f"❌ CRITICAL Error on Episode {episode_num}: {str(e)}\n🛑 Stopping generation to protect the server."
                bot.send_message(chat_id, error_msg)
                print(error_msg)
                break
                
            finally:
                if os.path.exists(final_mp3): os.remove(final_mp3)
                if os.path.exists(list_file_path): os.remove(list_file_path)
                for cf in chunk_files_to_delete:
                    if os.path.exists(cf): os.remove(cf)
                    
        else:
            bot.send_message(chat_id, "✅ Entire book finished! Ready for the next file.")

    except Exception as e:
        bot.reply_to(message, f"❌ A fatal error occurred: {str(e)}")

bot.infinity_polling()
