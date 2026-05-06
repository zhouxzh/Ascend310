import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

# Embedding model
EMBEDDING_DIM = 384
MAX_SEQ_LENGTH = 256
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
ONNX_MODEL_PATH = os.path.join(MODEL_DIR, "embedding_model.onnx")
OM_MODEL_PATH = os.path.join(MODEL_DIR, "embedding_model.om")

# FAISS
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "knowledge_index.faiss")
DOCS_PATH = os.path.join(DATA_DIR, "documents.json")
SAMPLE_KNOWLEDGE_PATH = os.path.join(DATA_DIR, "sample_knowledge.txt")
SAMPLE_FAQ_PATH = os.path.join(DATA_DIR, "sample_faq.json")
CHAT_HISTORY_PATH = os.path.join(DATA_DIR, "chat_history.json")

# Retrieval
TOP_K_RETRIEVAL = 3
SIMILARITY_THRESHOLD = 0.3
CHUNK_SIZE = 256
CHUNK_OVERLAP = 50

# NPU
NPU_DEVICE_ID = 0

# Cloud LLM (optional)
CLOUD_LLM_ENABLED = False
CLOUD_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"
CLOUD_LLM_MODEL = "gpt-3.5-turbo"
CLOUD_LLM_KEY = ""

# Voice
VOICE_ENABLED = True
VOICE_LANGUAGE = "zh-CN"
TTS_RATE = 160
TTS_VOLUME = 0.8

# Conversation
MAX_HISTORY_TURNS = 10
