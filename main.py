import cv2
import clip
import typer
import torch
import chromadb
import whisperx
import warnings
import numpy as np
from PIL import Image
from rich import print

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
    module="face_recognition_models",
)

import face_recognition
from moviepy.video.io.VideoFileClip import VideoFileClip
from sentence_transformers import SentenceTransformer

app = typer.Typer()

device = "cpu"
embedder = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache_folder="./models/sentence-transformers",
)
clip_model, preprocess = clip.load(
    "ViT-B/32",
    device=device,
    download_root="./models/clip",
)
chroma_client = chromadb.PersistentClient(path="./chroma_data")
voice_collection = chroma_client.get_or_create_collection(name="voiceEmbeddings")
scene_collection = chroma_client.get_or_create_collection(name="sceneEmbeddings")
actor_collection = chroma_client.get_or_create_collection(name="actorCollection")

@app.command()
def videoindex(path: str):

    video = VideoFileClip(path)
    video_audio = video.audio
    audio = "audio.wav"
    video_audio.write_audiofile(audio)

    print("[bold red]Video Indexing...[/bold red]")

    print("[green]Audio Indexing...[/green]")

    batch_size = 4
    compute_type = "int8"

    whisper_model = whisperx.load_model(
        "large-v2",
        device,
        compute_type=compute_type,
        download_root="./models",
    )

    audio = whisperx.load_audio(audio)

    result = whisper_model.transcribe(audio, batch_size=batch_size, language="ur")

    model_a, metadata = whisperx.load_align_model(language_code="ur", device=device, model_dir="./torch")
    result = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=False)

    segments = result["segments"]
    
    id = 0
    phrase_length = 5
    for segment in segments:
        words = segment["words"]
        for i in range(0, len(words), phrase_length):
            phrase_segments = words[i:i+phrase_length]
            phrase = " ".join(seg["word"] for seg in phrase_segments)
            start_time = phrase_segments[0]["start"]

            print(phrase)
            print(start_time)

            embedding = embedder.encode(phrase, convert_to_tensor=True)
            voice_collection.add(ids=[str(id)], embeddings=[embedding.tolist()], metadatas=[{"start": start_time}])
            id += 1
    
    print("[green]Audio Indexing Complete !!![/green]")

    print("[green]Scene Indexing...[/green]")
    
    id = 0
    time = 0.0

    video = cv2.VideoCapture(path)

    fps = video.get(cv2.CAP_PROP_FPS)
    frame_time = 1 / fps

    while True:
        ret, frame = video.read()
        if not ret:
            break
        # Convert OpenCV BGR image to RGB and then to PIL Image
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image = preprocess(image).unsqueeze(0).to(device)

        with torch.no_grad():
            image_features = clip_model.encode_image(image)
            image_features /= image_features.norm(dim=-1, keepdim=True)

        embedding_vector = image_features.cpu().numpy().tolist()[0]
        scene_collection.add(ids=[f"{id}"], embeddings=[embedding_vector], metadatas=[{"time": time}])

        id += 1
        time += frame_time

    video.release()

    print("[green]Scene Indexing Complete !!![/green]")

    print("[green]Actor Indexing...[/green]")

    FACE_MATCH_THRESHOLD = 0.55
    HISTORY_SIZE = 5

    known_face_encodings = []
    known_face_ids = []
    next_id = 1
    face_history = {}

    def get_best_match(face_encoding):
        """Finds the best matching face with history-based voting."""
        if not known_face_encodings:
            return None

        distances = face_recognition.face_distance(known_face_encodings, face_encoding)
        best_match_idx = distances.argmin()

        if distances[best_match_idx] < FACE_MATCH_THRESHOLD:
            return best_match_idx
        return None

    time = 0.0
    video = cv2.VideoCapture(path)
    fps = video.get(cv2.CAP_PROP_FPS)
    frame_time = 1 / fps
    faces = []

    while True:
        ret, frame = video.read()

        if not ret:
            break

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations, num_jitters=2)

        for face_encoding, face_location in zip(face_encodings, face_locations):
            match_idx = get_best_match(face_encoding)

            if match_idx is not None:
                face_id = known_face_ids[match_idx]
                
                
                if face_id in face_history:
                    face_history[face_id].append(face_encoding)
                    if len(face_history[face_id]) > HISTORY_SIZE:
                        face_history[face_id].pop(0)
                    
                    avg_encoding = np.mean(face_history[face_id], axis=0)
                    known_face_encodings[match_idx] = avg_encoding
                else:
                    face_history[face_id] = [face_encoding]
            else:
                face_id = f"{next_id}"
                next_id += 1
                known_face_encodings.append(face_encoding)
                known_face_ids.append(face_id)
                face_history[face_id] = [face_encoding]

            data = {
                "time": round(time, 3),
                "face_encoding": face_encoding,
                "face_location": face_location,
                "actor_id": face_id
            }
            faces.append(data)

        time += frame_time

    video.release()

    buckets = {}
    for face in faces:
        actor_id = face["actor_id"]
        if actor_id not in buckets:
            buckets[actor_id] = []
        buckets[actor_id].append(face)

    for actor_id, face_group in buckets.items():
        if len(face_group) > 3:  
            actor_metadata = []
            for face_data in face_group:
                actor_metadata.append({
                    "time": face_data["time"],
                    "face_location": face_data["face_location"]
                })

            times_str = ",".join(str(d["time"]) for d in actor_metadata)
            face_str = ",".join(str(d["face_location"]) for d in actor_metadata)
            actor_collection.add(
                ids=[f"{actor_id}"], 
                documents=["-"], 
                metadatas=[{"time": times_str, "face_location": face_str}]
            )
    
    print("[green]Actor Indexing...[/green]")

    print("[bold red]Video Indexing Complete !!![/bold red]")

@app.command()
def dialogue(dialogue: str):

    print("[green]Searching dialogue...[/green]")

    query = dialogue
    query_embedding = embedder.encode(query, convert_to_tensor=True)

    result = voice_collection.query(query_embeddings=[query_embedding.tolist()], include=["metadatas"], n_results=1)
    time = result["metadatas"][0][0]["start"]

    print("[green]Dialogue found !!![/green]")

    return time

@app.command()
def scene(scene: str):

    print("[green]Searching scene...[/green]")

    query = scene
    query = clip.tokenize([query]).to(device)
 
    with torch.no_grad():
        query_features = clip_model.encode_text(query)
        query_features /= query_features.norm(dim=-1, keepdim=True)
     
    query_embedding = query_features.cpu().numpy().tolist()[0]
 
    result = scene_collection.query(query_embeddings=[query_embedding],include=["metadatas"],n_results=1)
 
    time = result["metadatas"][0][0]["time"]

    print("[green]Scene found...[/green]")

    return time

@app.command()
def actor(id: str, input_path: str, output_path: str = "output.mp4"):
    metadata = actor_collection.get(ids=[id], include=["metadatas"])['metadatas'][0]
    times = [float(t) for t in metadata["time"].split(",")]
    face_locs = [eval(loc + ")") if not loc.endswith(")") else eval(loc) for loc in metadata["face_location"].split("),")]

    video = cv2.VideoCapture(input_path)
    fps = video.get(cv2.CAP_PROP_FPS)
    width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'avc1'), fps, (width, height))

    frame_targets = {round(t * fps): loc for t, loc in zip(times, face_locs)}
    frame_idx = 0

    while True:
        ret, frame = video.read()
        if not ret:
            break

        if frame_idx in frame_targets:
            top, right, bottom, left = frame_targets[frame_idx]
            color = (0, 255, 0)
            thickness = max(2, int(height / 200))  
            font_scale = max(0.5, height / 1000)  
            
            cv2.rectangle(frame, (left, top), (right, bottom), color, thickness)
            cv2.putText(frame, f"Actor {id}", (left, top - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness)

        writer.write(frame)
        frame_idx += 1

    video.release()
    writer.release()
    print(f"[green]Video saved as {output_path}[/green]")


if __name__ == "__main__":
    app()
