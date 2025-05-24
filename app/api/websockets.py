      
# app/api/websockets.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Dict, List
from app.api import deps
from app.models.user import UserPublic
from app.services import matchmaking_service, game_service
from app.models.game import PlayerAction, GameState, GameStatePlayer, SentencePromptPublic # Pydantic models
# from app.models.user import UserPublic # No longer needed directly here if names come from matchmaking
from app.crud import crud_game_content # Keep for fetching sentence prompts
# from app.crud import crud_user # No longer fetching users here
import json
import time
import asyncio

router = APIRouter()

class GameConnectionManager:
    def __init__(self):
        # game_id -> user_id (int) -> WebSocket
        self.active_connections: Dict[str, Dict[int, WebSocket]] = {} # Use int for user_id

    async def connect(self, websocket: WebSocket, game_id: str, user_id: int): # Use user_id (int)
        await websocket.accept()
        if game_id not in self.active_connections:
            self.active_connections[game_id] = {}
        # Check if player is already connected
        if user_id in self.active_connections[game_id]:
            print(f"Player {user_id} reconnected to game {game_id}, closing old connection.")
            try:
                 await self.active_connections[game_id][user_id].close(code=status.WS_1001_GOING_AWAY, reason="New connection established")
            except Exception as e:
                 print(f"Error closing old websocket for {user_id}: {e}")
        self.active_connections[game_id][user_id] = websocket
        print(f"Player {user_id} connected to game {game_id}. Current players: {list(self.active_connections[game_id].keys())}")

    def disconnect(self, game_id: str, user_id: int): # Use user_id (int)
        if game_id in self.active_connections and user_id in self.active_connections[game_id]:
            del self.active_connections[game_id][user_id]
            print(f"Player {user_id} removed from active connections for game {game_id}")
            if not self.active_connections[game_id]:
                print(f"Game {game_id} has no players left, removing from active connections.")
                del self.active_connections[game_id]
                matchmaking_service.cleanup_game(game_id) # Clean up game state too

    async def broadcast_to_game(self, game_id: str, message: dict, exclude_player_id: int | None = None): # Use exclude_player_id (int)
        if game_id in self.active_connections:
            tasks = []
            player_ids = list(self.active_connections[game_id].keys())
            print(f"Broadcasting to game {game_id} (players: {player_ids}): {message.get('type')}")
            for player_id, connection in self.active_connections[game_id].items():
                if player_id != exclude_player_id:
                     tasks.append(self._send_json_safe(connection, message, player_id, game_id))
            if tasks:
                await asyncio.gather(*tasks)

    async def send_to_player(self, game_id: str, user_id: int, message: dict): # Use user_id (int)
        if game_id in self.active_connections and user_id in self.active_connections[game_id]:
            connection = self.active_connections[game_id][user_id]
            print("Sending message to player", user_id, "in game", game_id, ":", message.get("type"))
            await self._send_json_safe(connection, message, user_id, game_id) # Pass user_id

    async def _send_json_safe(self, connection: WebSocket, message: dict, user_id: int, game_id: str): # Use user_id (int)
         try:
             await connection.send_json(message)
         except Exception as e:
             print(f"Error sending message to {user_id} in game {game_id}: {e}. Disconnecting.")
             self.disconnect(game_id, user_id) # Use user_id
             await self.broadcast_to_game(game_id, {"type": "player_disconnected", "player_id": user_id}, exclude_player_id=user_id) # Use user_id


game_manager = GameConnectionManager()

@router.websocket("/ws/game/{game_id}")
async def game_websocket(
    websocket: WebSocket,
    game_id: str,
    # --- Use user_id (int) from client ---
    user_id: int = Query(..., description="User ID matching the one used in matchmaking"),
    db: Session = Depends(deps.get_db)
):
    print(f"WebSocket connection attempt: game_id={game_id}, user_id={user_id}")
    player_id = user_id # Use the integer ID

    # --- Check Game Info (using integer ID) ---
    game_info = matchmaking_service.get_game_info(game_id)
    if not game_info:
        print(f"Game {game_id} not found.")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game not found")
        return
    # Check if player ID is in the list of players for this game
    if player_id not in game_info.get("players", []):
         print(f"Player {player_id} not part of game {game_id}. Expected: {game_info.get('players', [])}")
         await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Player not in game")
         return

    # --- Connect Player (using integer ID) ---
    await game_manager.connect(websocket, game_id, player_id)

    # --- Game Setup / State Synchronization ---
    try:
        # Retrieve the latest game info again after connecting
        current_game_info = matchmaking_service.get_game_info(game_id)
        if not current_game_info: # Should not happen if initial check passed, but safety first
             print(f"Error: Game {game_id} disappeared after connection.")
             await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game disappeared")
             return

        game_status = current_game_info.get("status")

        if game_status == "matched": # Status set by matchmaking_service
            if len(game_manager.active_connections.get(game_id, {})) == 2: # Both players connected
                print(f"Both players connected for game {game_id}. Initializing full game state...")

                # --- Abstracted Sentence Selection ---
                sentence_prompt_db = crud_game_content.get_random_sentence_prompt(db)
                if not sentence_prompt_db:
                    await game_manager.broadcast_to_game(game_id, {"type": "error", "message": "Failed to load game content."})
                    print(f"ERROR: Failed to load sentence prompt for game {game_id}")
                    matchmaking_service.cleanup_game(game_id)
                    # Close both connections if possible
                    p_ids = list(game_manager.active_connections.get(game_id, {}).keys())
                    for p_id in p_ids:
                         conn = game_manager.active_connections.get(game_id,{}).get(p_id)
                         if conn: await conn.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game content error")
                    return
                sentence_prompt_pydantic = SentencePromptPublic.model_validate(sentence_prompt_db)
                # --- End Sentence Selection ---

                # --- Construct Full Initial Game State ---
                player_ids_from_matchmaking = current_game_info["players"] # List of int IDs [id1, id2]
                p1_server_id = player_ids_from_matchmaking[0] # This is the actual server ID for player 1
                p2_server_id = player_ids_from_matchmaking[1] # This is the actual server ID for player 2

                p1_details = UserPublic(**current_game_info["player_details"][p1_server_id])
                p2_details = UserPublic(**current_game_info["player_details"][p2_server_id])

                # Prepare player state objects as expected by the client's GameStatePlayer
                player1_game_state_player = GameStatePlayer(
                    id=p1_server_id, # Use the server ID
                    name=p1_details.username or f"Player {p1_server_id}",
                    score=0,
                    mistakes_in_current_round=0,
                    words_played=[]
                ).model_dump()

                player2_game_state_player = GameStatePlayer(
                    id=p2_server_id, # Use the server ID
                    name=p2_details.username or f"Player {p2_server_id}",
                    score=0,
                    mistakes_in_current_round=0,
                    words_played=[]
                ).model_dump()

                initial_game_state_dict_for_service = { # This is for internal service storage
                    "game_id": game_id,
                    "players": { # Stored by server ID
                        p1_server_id: player1_game_state_player,
                        p2_server_id: player2_game_state_player,
                    },
                    "status": "in_progress",
                    "current_player_id": p1_server_id,
                    "current_round": 1,
                    "max_rounds": 3,
                    "sentence_prompt": sentence_prompt_pydantic.model_dump(),
                    "words_played_this_round_all": [],
                    "is_waiting_for_opponent": False, # Game is active now
                    "last_action_timestamp": time.time(),
                }
                matchmaking_service.update_game_state(game_id, initial_game_state_dict_for_service)

                # --- CORRECTED PAYLOAD FOR CLIENT ---
                client_payload = {
                    "game_id": game_id,
                    "current_sentence": sentence_prompt_pydantic.sentence_text,
                    "prompt": sentence_prompt_pydantic.prompt_text,
                    "word_to_replace": sentence_prompt_pydantic.target_word,
                    "round": initial_game_state_dict_for_service["current_round"],

                    # --- ADD THESE LINES ---
                    "player1_server_id": str(p1_server_id), # Convert to string if IDs are int
                    "player2_server_id": str(p2_server_id), # Convert to string if IDs are int
                    # --- END ADDED LINES ---

                    "player1_state": player1_game_state_player, # The GameStatePlayer model_dump()
                    "player2_state": player2_game_state_player, # The GameStatePlayer model_dump()

                    "current_player_id": str(initial_game_state_dict_for_service["current_player_id"]), # Send string ID
                    "player1_words": player1_game_state_player["words_played"],
                    "player2_words": player2_game_state_player["words_played"],
                    "game_active": True # Explicitly tell client the game is active
                }

                await game_manager.broadcast_to_game(game_id, {
                    "type": "game_start",
                    "payload": client_payload # Use the corrected payload
                })
                print(f"Game {game_id} started. Broadcasting initial state with player server IDs.")
                print(f"Game {game_id} started. Broadcasting initial state.")

            elif game_status == "in_progress":
                print(f"Player {player_id} reconnected to game {game_id} in progress. Sending current state.")
                current_full_state = matchmaking_service.get_full_game_state(game_id)
                # ... (error handling for current_full_state) ...

                # Ensure player IDs are present and correctly fetched
                server_player_ids_from_state = list(current_full_state["players"].keys())
                if len(server_player_ids_from_state) < 2:
                    print(f"Error: Game state for {game_id} has less than 2 player IDs: {server_player_ids_from_state}")
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Corrupt game state (player IDs)")
                    return

                # It's safer to not assume order if you just get keys. Determine p1_id and p2_id
                # based on some convention or ensure they are stored consistently.
                # For now, assuming the client can handle whatever IDs are in player1_state/player2_state fields
                # as long as player1_server_id and player2_server_id are also present.
                # Let's assume the order from matchmaking is preserved in current_full_state["players"] keys.
                # Or, better, if current_game_info still has the original player list.
                original_player_order = matchmaking_service.get_game_info(game_id)["players"] # Fetch original order
                p1_server_id_reconnect = original_player_order[0]
                p2_server_id_reconnect = original_player_order[1]


                current_prompt = SentencePromptPublic(**current_full_state["sentence_prompt"])

                # --- CORRECTED PAYLOAD FOR RECONNECT ---
                reconnect_payload = {
                    "game_id": game_id,
                    "current_sentence": current_prompt.sentence_text,
                    "prompt": current_prompt.prompt_text,
                    "word_to_replace": current_prompt.target_word,
                    "round": current_full_state["current_round"],

                    # --- ADD THESE LINES ---
                    "player1_server_id": str(p1_server_id_reconnect),
                    "player2_server_id": str(p2_server_id_reconnect),
                    # --- END ADDED LINES ---

                    "player1_state": current_full_state["players"][p1_server_id_reconnect],
                    "player2_state": current_full_state["players"][p2_server_id_reconnect],

                    "current_player_id": str(current_full_state["current_player_id"]),
                    "player1_words": current_full_state["players"][p1_server_id_reconnect]["words_played"],
                    "player2_words": current_full_state["players"][p2_server_id_reconnect]["words_played"],
                    "game_active": True
                }

                await game_manager.send_to_player(game_id, player_id, {
                    "type": "game_state",
                    "payload": reconnect_payload # Use the corrected payload
                })
            else:
                print(f"Player {player_id} connected, waiting for opponent in game {game_id}.")
                await game_manager.send_to_player(game_id, player_id, {"type": "status", "message": "Waiting for opponent to connect..."})

        elif game_status == "in_progress":
            print(f"Player {player_id} reconnected to game {game_id} in progress. Sending current state.")
            # --- Send current full state ONLY to rejoining player ---
            current_full_state = matchmaking_service.get_full_game_state(game_id)
            if not current_full_state:
                 print(f"Error: Full game state not found for {game_id} on reconnect.")
                 await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game state missing")
                 return

            p_ids = list(current_full_state["players"].keys())
            p1_id, p2_id = p_ids[0], p_ids[1] # Get the integer IDs
            current_prompt = SentencePromptPublic(**current_full_state["sentence_prompt"])

            await game_manager.send_to_player(game_id, player_id, {
                 "type": "game_state", # Use 'game_state' type for updates/reconnects
                 "payload": {
                      "game_id": game_id,
                      "current_sentence": current_prompt.sentence_text,
                      "prompt": current_prompt.prompt_text,
                      "word_to_replace": current_prompt.target_word,
                      "round": current_full_state["current_round"],
                      "player1_state": current_full_state["players"][p1_id],
                      "player2_state": current_full_state["players"][p2_id],
                      "current_player_id": current_full_state["current_player_id"],
                      "player1_words": current_full_state["players"][p1_id]["words_played"],
                      "player2_words": current_full_state["players"][p2_id]["words_played"],
                 }
            })
        else:
             print(f"Player {player_id} connected to game {game_id} with unexpected status: {game_status}")
             await game_manager.send_to_player(game_id, player_id, {"type": "error", "message": f"Game status is unexpected: {game_status}"})

        # --- Main Loop for Receiving Player Actions ---
        while True:
            data = await websocket.receive_json()
            print(f"Received action from {player_id} in game {game_id}: {data}") # Use integer ID

            action_type = data.get("action_type")
            payload = data.get("payload", {})
            if not action_type:
                 await game_manager.send_to_player(game_id, player_id, {"type": "error", "message": "Invalid action format: 'type' missing."})
                 continue

            # --- Retrieve current full game state ---
            current_game_state_dict = matchmaking_service.get_full_game_state(game_id)
            if not current_game_state_dict or current_game_state_dict.get("status") != "in_progress":
                print(f"Game {game_id} not in progress or not found. Ignoring action.")
                await game_manager.send_to_player(game_id, player_id, {"type": "error", "message": "Game not active."})
                continue

            # --- Process Action (using integer player_id) ---
            is_players_turn = current_game_state_dict.get("current_player_id") == player_id

            if action_type == "submit_word":
                if not is_players_turn:
                     await game_manager.send_to_player(game_id, player_id, {"type": "error", "message": "Not your turn."})
                     continue

                word = payload.get("word", "").strip().lower()
                if not word:
                    await game_manager.send_to_player(game_id, player_id, {"type": "validation_result", "payload": {"word": word, "is_valid": False, "message": "Word cannot be empty."}})
                    continue

                # Check if already played
                if word in current_game_state_dict.get("words_played_this_round_all", []):
                    current_game_state_dict["players"][player_id]["mistakes_in_current_round"] += 1
                    mistakes = current_game_state_dict["players"][player_id]["mistakes_in_current_round"]
                    await game_manager.send_to_player(game_id, player_id, {"type": "validation_result", "payload": {"word": word, "is_valid": False, "message": "Word already played this round. Mistake!"}})
                    if mistakes >= game_service.MAX_MISTAKES: pass # TODO: Handle Round Over
                    else: pass # TODO: Broadcast state update
                    matchmaking_service.update_game_state(game_id, current_game_state_dict)
                    continue

                # --- Actual Word Validation ---
                prompt_obj = SentencePromptPublic(**current_game_state_dict["sentence_prompt"])
                is_valid_replacement = game_service.validate_word_against_prompt(
                    word, prompt_obj.target_word, prompt_obj.prompt_text, prompt_obj.sentence_text
                )

                if is_valid_replacement:
                    current_game_state_dict["players"][player_id]["words_played"].append(word) # Store original case maybe
                    current_game_state_dict["words_played_this_round_all"].append(word)
                    await game_manager.send_to_player(game_id, player_id, {"type": "validation_result", "payload": {"word": word, "is_valid": True, "message": "Good word!"}})

                    # Switch turn (using integer IDs)
                    all_player_ids = list(current_game_state_dict["players"].keys())
                    current_idx = all_player_ids.index(player_id)
                    next_idx = (current_idx + 1) % len(all_player_ids)
                    next_player_id = all_player_ids[next_idx]
                    current_game_state_dict["current_player_id"] = next_player_id
                    current_game_state_dict["last_action_timestamp"] = time.time()
                    matchmaking_service.update_game_state(game_id, current_game_state_dict)

                    # # Broadcast updated state
                    # all_player_ids_in_state = list(current_game_state_dict["players"].keys()) # These are the server IDs
                    # original_player_order_broadcast = matchmaking_service.get_game_info(game_id)["players"] # Fetch original order for consistent p1/p2
                    # p1_id_broadcast = original_player_order_broadcast[0]
                    # p2_id_broadcast = original_player_order_broadcast[1]


                    # broadcast_payload = {
                    #     "current_player_id": str(current_game_state_dict["current_player_id"]),
                    #     "player1_server_id": str(p1_id_broadcast), # Add this
                    #     "player2_server_id": str(p2_id_broadcast), # Add this
                    #     "player1_state": current_game_state_dict["players"][p1_id_broadcast],
                    #     "player2_state": current_game_state_dict["players"][p2_id_broadcast],
                    #     "player1_words": current_game_state_dict["players"][p1_id_broadcast]["words_played"],
                    #     "player2_words": current_game_state_dict["players"][p2_id_broadcast]["words_played"],
                    #     "game_active": True, # Add this
                    #     # You might also need to send round, sentence, prompt if they can change mid-game by server
                    #     "round": current_game_state_dict["current_round"],
                    #     # current_sentence, prompt, word_to_replace usually don't change mid-round
                    # }
                    # current_sent_prompt_obj = SentencePromptPublic(**current_game_state_dict["sentence_prompt"])
                    # broadcast_payload["current_sentence"] = current_sent_prompt_obj.sentence_text
                    # broadcast_payload["prompt"] = current_sent_prompt_obj.prompt_text
                    # broadcast_payload["word_to_replace"] = current_sent_prompt_obj.target_word


                    # await game_manager.broadcast_to_game(game_id, {
                    #     "type": "game_state",
                    #     "payload": broadcast_payload
                    # })

                    original_player_order_broadcast = matchmaking_service.get_game_info(game_id)["players"]
                    p1_id_for_payload = original_player_order_broadcast[0]
                    p2_id_for_payload = original_player_order_broadcast[1]

                    turn_change_payload = {
                        "opponent_played_word": word, # The word the other player submitted
                        "opponent_word_is_valid": True, # Since we are in this block
                        "game_id": game_id, # Redundant but can be useful
                        "current_sentence": prompt_obj.sentence_text,
                        "prompt": prompt_obj.prompt_text,
                        "word_to_replace": prompt_obj.target_word,
                        "round": current_game_state_dict["current_round"],

                        # Full state needed for the player whose turn it now is
                        "player1_server_id": str(p1_id_for_payload),
                        "player2_server_id": str(p2_id_for_payload),
                        "player1_state": current_game_state_dict["players"][p1_id_for_payload],
                        "player2_state": current_game_state_dict["players"][p2_id_for_payload],
                        "current_player_id": str(next_player_id), # Crucially, this is the ID of the recipient
                        "player1_words": current_game_state_dict["players"][p1_id_for_payload]["words_played"],
                        "player2_words": current_game_state_dict["players"][p2_id_for_payload]["words_played"],
                        "game_active": True
                    }

                    await game_manager.send_to_player(
                        game_id,
                        next_player_id, # Send ONLY to the player whose turn it now is
                        {
                            "type": "opponent_turn_ended", # New message type
                            "payload": turn_change_payload
                        }
                    )
                    print(f"Sent opponent_turn_ended to player {next_player_id} after {player_id} played '{word}'")

                else: # Invalid word - Mistake
                    current_game_state_dict["players"][player_id]["mistakes_in_current_round"] += 1
                    mistakes = current_game_state_dict["players"][player_id]["mistakes_in_current_round"]
                    await game_manager.send_to_player(game_id, player_id, {"type": "validation_result", "payload": {"word": word, "is_valid": False, "message": "Not a valid replacement. Mistake!"}})
                    if mistakes >= game_service.MAX_MISTAKES: pass # TODO: Handle Round Over
                    
                    matchmaking_service.update_game_state(game_id, current_game_state_dict)

            elif action_type == "send_emoji":
                emoji = payload.get("emoji")
                if emoji:
                    print(f"Broadcasting emoji {emoji} from {player_id} in game {game_id}")
                    await game_manager.broadcast_to_game(
                        game_id,
                        {"type": "opponent_action", "payload": {"action": "send_emoji", "emoji": emoji}},
                        exclude_player_id=player_id # Exclude sender (use int ID)
                    )
            elif action_type == "timeout":
                 if is_players_turn:
                      current_game_state_dict["players"][player_id]["mistakes_in_current_round"] += 1
                      mistakes = current_game_state_dict["players"][player_id]["mistakes_in_current_round"]
                      print(f"Player {player_id} timed out in game {game_id}. Mistakes: {mistakes}")
                      if mistakes >= game_service.MAX_MISTAKES: pass # TODO: Handle Round Over
                      else:
                          # Switch turn on timeout
                          all_player_ids = list(current_game_state_dict["players"].keys())
                          current_idx = all_player_ids.index(player_id)
                          next_idx = (current_idx + 1) % len(all_player_ids)
                          current_game_state_dict["current_player_id"] = all_player_ids[next_idx]
                          current_game_state_dict["last_action_timestamp"] = time.time()
                          # TODO: Broadcast state update
                          pass
                      matchmaking_service.update_game_state(game_id, current_game_state_dict)
                 else:
                      print(f"Player {player_id} sent timeout but it's not their turn.")

            else:
                 print(f"Unknown action type received from {player_id}: {action_type}")
                 await game_manager.send_to_player(game_id, player_id, {"type": "error", "message": f"Unknown action type: {action_type}"})


    except WebSocketDisconnect:
        print(f"WebSocket disconnected for player {player_id} in game {game_id}.") # Use int ID
        game_manager.disconnect(game_id, player_id)
        await game_manager.broadcast_to_game(game_id, {"type": "player_disconnected", "player_id": player_id}, exclude_player_id=player_id) # Use int ID
    except Exception as e:
        print(f"!!! ERROR during WebSocket communication for player {player_id} in game {game_id}: {e}") # Use int ID
        import traceback
        traceback.print_exc()
        try: await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server error")
        except Exception: pass
        game_manager.disconnect(game_id, player_id)
        await game_manager.broadcast_to_game(game_id, {"type": "player_disconnected", "player_id": player_id, "reason": "server_error"}, exclude_player_id=player_id) # Use int ID

    