# tests/test_integration_full_game.py

import asyncio
import uuid
import logging
import json
import random
from typing import Any, Dict, Optional, Set

import httpx
import pytest
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

# --- Import Application Settings ---
try:
    from app.core.config import settings
    import google.generativeai as genai
    GEMINI_ENABLED = bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "YOUR_GEMINI_API_KEY_HERE")
    if GEMINI_ENABLED:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except (ImportError, Exception) as e:
    print(f"Could not configure Gemini, will use fallback words. Error: {e}")
    GEMINI_ENABLED = False


# Configure logging for the test script
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("GameTest")

# --- Configuration ---
BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000"
RANDOM_WORDS = [
    "epic", "legendary", "colossal", "minute", "ancient", "futuristic",
    "silent", "deafening", "radiant", "abyssal", "ethereal", "voracious",
    "zealous", "quixotic", "ephemeral", "gargantuan", "cosmic", "atomic"
]
MISTAKE_CHANCE = 0.40 # 40% chance to make a mistake each turn
MISTAKE_CONSECUTIVE_CHANCE = 0.75 # 75% chance to make a consecutive mistake
MISTAKE_DUPLICATE_CHANGE = 0.1 # 10% chance to repeat a word
# --- Gemini Helper Function ---
async def get_gemini_word_suggestion(
    sentence: str, target_word: str, prompt: str, words_to_avoid: Set[str]
) -> str:
    fallback = random.choice([w for w in RANDOM_WORDS if w not in words_to_avoid] or ["test"])
    if not GEMINI_ENABLED:
        log.warning("Gemini is not enabled or configured. Using fallback word.")
        return fallback
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-lite')
        avoid_str = ", ".join(list(words_to_avoid))
        gemini_prompt = f"""
You are a creative player in a word game. Your task is to provide a SINGLE word that is a creative replacement.
The sentence is: "{sentence}"
The word to replace is: "{target_word}"
The prompt is: "{prompt}"
Do NOT use any of these words: "{avoid_str}"
Provide only the single replacement word in your response, with no extra text or quotes.
"""
        log.info(f"Querying Gemini with prompt: '{prompt}'...")
        response = await model.generate_content_async(gemini_prompt)
        suggested_word = response.text.strip().replace('"', '').replace('.', '').split(' ')[0]
        if not suggested_word or suggested_word.lower() in words_to_avoid:
            log.warning(f"Gemini suggested an empty or repeated word ('{suggested_word}'). Using fallback.")
            return fallback
        log.info(f"Gemini suggested: '{suggested_word}'")
        return suggested_word
    except Exception as e:
        log.error(f"Error calling Gemini API: {e}", exc_info=True)
        return fallback

# --- Helper Class to Simulate a Game Client ---
class GameClient:
    def __init__(self, client_id: str):
        self.client_id, self.password = client_id, "password123"
        self.jwt: Optional[str] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self.game_id: Optional[str] = None
        self.player_id: Optional[int] = None
        self.ws_conn: Optional[WebSocketClientProtocol] = None
        self.ws_listener_task: Optional[asyncio.Task] = None
        self.received_events = asyncio.Queue()

    async def register_and_login(self, client: httpx.AsyncClient):
        resp = await client.post("/api/v1/auth/device-login", json={"client_provided_id": self.client_id, "client_generated_password": self.password})
        resp.raise_for_status()
        data = resp.json()
        self.jwt, self.user_info, self.player_id = data['access_token'], data['user'], data['user']['id']
        log.info(f"[{self.client_id}] Login successful. Player ID: {self.player_id}")

    async def find_match(self, client: httpx.AsyncClient) -> str:
        log.info(f"[{self.client_id}] Polling for a match...")
        while True:
            resp = await client.get("/api/v1/matchmaking/find", params={"requested_language": "en"}, headers={'Authorization': f'Bearer {self.jwt}'})
            resp.raise_for_status()
            data = resp.json()
            if data['status'] == 'matched':
                self.game_id = data['game_id']
                log.info(f"[{self.client_id}] Match found! Game ID: {self.game_id}")
                return self.game_id
            await asyncio.sleep(1)

    async def connect_websocket(self):
        ws_uri = f"{WS_URL}/ws/game/{self.game_id}?token={self.jwt}"
        self.ws_conn = await websockets.connect(ws_uri)
        self.ws_listener_task = asyncio.create_task(self._listen_for_events())
        log.info(f"[{self.client_id}] WebSocket connection established.")

    async def _listen_for_events(self):
        try:
            async for message in self.ws_conn:
                event = json.loads(message)
                log.info(f"[{self.client_id}] Received event: {event['type']} {event['payload']}")
                await self.received_events.put(event)
        except ConnectionClosed as e:
            log.info(f"[{self.client_id}] WebSocket closed: {e.code}")

    async def get_next_event(self, timeout: int = 15) -> Dict[str, Any]:
        return await asyncio.wait_for(self.received_events.get(), timeout)

    async def wait_for_event_type(self, event_type: str, timeout: int = 15) -> Dict[str, Any]:
        log.info(f"[{self.client_id}] Waiting for event of type '{event_type}'...")
        start_time = asyncio.get_event_loop().time()
        while True:
            remaining_time = timeout - (asyncio.get_event_loop().time() - start_time)
            if remaining_time <= 0:
                pytest.fail(f"[{self.client_id}] Timed out waiting for event '{event_type}'")
            event = await self.get_next_event(int(remaining_time))
            if event['type'] == event_type:
                log.info(f"[{self.client_id}] Successfully found event '{event_type}'.")
                return event
            else:
                log.warning(f"[{self.client_id}] Ignored unexpected event '{event['type']}' while waiting for '{event_type}'.")

    async def send_action(self, action_type: str, payload: Dict = None):
        action = {"action_type": action_type, "payload": payload or {}}
        log.info(f"[{self.client_id}] Sending action: {action_type} with payload: {payload or {}}")
        await self.ws_conn.send(json.dumps(action))

    async def close(self):
        if self.ws_listener_task and not self.ws_listener_task.done(): self.ws_listener_task.cancel()
        if self.ws_conn:
            await self.ws_conn.close()
        log.info(f"[{self.client_id}] Connection closed.")

@pytest.mark.asyncio
async def test_full_game_simulation():
    p1 = GameClient(f"test-client-{uuid.uuid4().hex[:8]}")
    p2 = GameClient(f"test-client-{uuid.uuid4().hex[:8]}")

    try:
        async with httpx.AsyncClient(base_url=BASE_URL) as client:
            await asyncio.gather(p1.register_and_login(client), p2.register_and_login(client))
            await asyncio.gather(p1.find_match(client), p2.find_match(client))
        
        assert p1.game_id == p2.game_id
        await asyncio.gather(p1.connect_websocket(), p2.connect_websocket())
        
        evt1, evt2 = await asyncio.gather(
            p1.wait_for_event_type('game_setup_ready'),
            p2.wait_for_event_type('game_setup_ready')
        )
        game_context = evt1['payload']
        
        await asyncio.gather(p1.send_action("client_ready"), p2.send_action("client_ready"))
        
        start1, start2 = await asyncio.gather(
            p1.wait_for_event_type('round_started'),
            p2.wait_for_event_type('round_started')
        )
        
        current_player_id = int(start1['payload']['current_player_id'])
        log.info(f"SUCCESS: Round 1 started. Current player is P:{current_player_id}")

        words_played, game_over = set(), False
        total_mistake_counter = {p1.player_id: 0, p2.player_id: 0}
        consecutive_mistake_counter = {p1.player_id: 0, p2.player_id: 0}


        while not game_over:
            active, opponent = (p1, p2) if current_player_id == p1.player_id else (p2, p1)
            log.info(f"--- TURN START: P:{active.player_id} | Total Mistakes: {total_mistake_counter[active.player_id]}, Consecutive: {consecutive_mistake_counter[active.player_id]} ---")

            make_mistake = words_played and ((random.random() < MISTAKE_CHANCE) or \
            (consecutive_mistake_counter[active.player_id] > 0 and random.random() < MISTAKE_CONSECUTIVE_CHANCE))


            # --- NEW: Logic to decide whether to make a mistake ---
            if make_mistake:

                if random.random() < MISTAKE_DUPLICATE_CHANGE:
                    # --- MISTAKE PATH: Submit a duplicate word ---
                    word_to_play = random.choice(list(words_played))
                    log.info(f"[{active.client_id}] Simulating a mistake by playing duplicate word: '{word_to_play}'")
                    
                    await active.send_action("submit_word", {"word": word_to_play})
                    
                    val_evt = await active.wait_for_event_type('validation_result')
                    assert val_evt['payload']['is_valid'] is False
                    assert "already played" in val_evt['payload']['message']
                    total_mistake_counter[active.player_id] += 1
                    consecutive_mistake_counter[active.player_id] += 1

                else:
                    # --- MISTAKE PATH: Submit a random word ---
                    log.info(f"[{active.client_id}] Simulating a mistake by playing random word")
                    word_to_play = random.choice(RANDOM_WORDS)

                    await active.send_action("submit_word", {"word": word_to_play})

                    val_evt = await active.wait_for_event_type('validation_result')

                    if val_evt['payload']['is_valid'] is True:
                        log.info("[{}] Word randomly fit the sentence and prompt: '{}'".format(active.client_id, word_to_play))
                        words_played.add(word_to_play)
                        consecutive_mistake_counter[active.player_id] = 0

                        opp_evt = await opponent.get_next_event()
                        assert opp_evt['type'] == 'opponent_turn_ended'
                        current_player_id = int(opp_evt['payload']['current_player_id'])
                        continue
                        
                    assert val_evt['payload']['is_valid'] is False
                    total_mistake_counter[active.player_id] += 1
                    consecutive_mistake_counter[active.player_id] += 1
            
            
                if total_mistake_counter[active.player_id] >= 3 or consecutive_mistake_counter[active.player_id] >= 2:
                    log.info(f"[{active.client_id}] Player made 3rd mistake. Expecting round to end.")
                    # Both players should get a round/game over event
                    end_evt_opp = await opponent.get_next_event()
                    assert end_evt_opp['type'] == 'opponent_mistake'
                    end_evt_active = await active.get_next_event()
                    
                    if end_evt_active['type'] == 'game_over':
                        game_over = True
                        continue
                    else: # New Round
                        assert end_evt_active['type'] == 'new_round_started'
                        log.info("--- NEW ROUND (after mistake) ---")
                        words_played.clear()
                        total_mistake_counter = {p1.player_id: 0, p2.player_id: 0}
                        consecutive_mistake_counter = {p1.player_id: 0, p2.player_id: 0}
                        game_context = end_evt_opp['payload']
                        await asyncio.gather(p1.send_action("client_ready"), p2.send_action("client_ready"))
                        rs1, _ = await asyncio.gather(p1.wait_for_event_type('round_started'), p2.wait_for_event_type('round_started'))
                        current_player_id = int(rs1['payload']['current_player_id'])
                else:
                    # Not the 3rd mistake or 2nd consecutive mistake, so turn should not change
                    opp_evt = await opponent.wait_for_event_type('opponent_mistake')
                    assert opp_evt['payload']['player_id'] == str(active.player_id)
                    log.info(f"[{active.client_id}] Mistake correctly handled. Turn remains with P:{current_player_id}")
            
            else:
                # --- VALID WORD PATH ---
                log.info(f"[{active.client_id}] Playing a valid word.")
                consecutive_mistake_counter[active.player_id] = 0
                
                word_to_play = await get_gemini_word_suggestion(
                    game_context.get('current_sentence'), game_context.get('word_to_replace'), 
                    game_context.get('prompt'), words_played
                )
                words_played.add(word_to_play.lower())
                
                await active.send_action("submit_word", {"word": word_to_play})
                
                val_evt = await active.wait_for_event_type('validation_result')
                if not val_evt['payload']['is_valid']:
                    pytest.fail(f"Word '{word_to_play}' was unexpectedly invalid: {val_evt['payload'].get('message')}")

                # Check what happened after the valid move
                opp_evt = await opponent.get_next_event()

                assert opp_evt['type'] == 'opponent_turn_ended'
                current_player_id = int(opp_evt['payload']['current_player_id'])
                
    
    finally:
        log.info("--- Test finished, cleaning up clients ---")
        await asyncio.gather(p1.close(), p2.close())