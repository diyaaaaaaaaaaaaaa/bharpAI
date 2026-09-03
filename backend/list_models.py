"""
Ask Google directly which models YOUR key can actually use, instead
of trusting any doc or blog post (they go stale within weeks right
now). Run this once whenever a model name stops working.

Run with: python list_models.py
"""
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not found. Set up your .env first.")

client = genai.Client(api_key=api_key)

print("Models your key can use with generateContent (the stable API we're using):\n")
for model in client.models.list():
    actions = getattr(model, "supported_actions", None) or []
    if "generateContent" in actions:
        print(f" - {model.name}")
