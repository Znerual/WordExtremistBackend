	  
# app/api/websockets.py
import json
import logging
import random
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, status, Query
from starlette.websockets import WebSocketState
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from app.api import deps
from app.db.session import SessionLocal
from app.services import matchmaking_service, game_service, bot_service
from app.models.game import GameState
import asyncio

logger = logging.getLogger("app.api.websockets")  # Logger for this module
router = APIRouter()

# A module-level dictionary to keep track of active timer tasks for each game.
# This avoids storing non-serializable asyncio.Task objects in the GameState.
active_turn_timers: Dict[str, asyncio.Task] = {}

async def _handle_timeout(game_id: str):
	"""
	The coroutine that runs when a timer expires.
	It gets its own database session to ensure it's valid.
	"""
	db = SessionLocal()
	try:
		logger.info(f"G:{game_id} - Turn timer expired. Processing timeout.")
		
		gs = matchmaking_service.get_full_game_state(game_id)
		if not gs or gs.status != "in_progress":
			logger.warning(f"G:{game_id} - Timeout triggered but game not in progress (status: {gs.status if gs else 'N/A'}). Aborting.")
			return

		player_who_timed_out = gs.current_player_id
		if player_who_timed_out is None:
			logger.error(f"G:{game_id} - Timeout triggered but current_player_id is None. Aborting.")
			return

		# Process the timeout action using the game service
		updated_gs, timeout_events = game_service.process_player_game_action(
			gs, player_who_timed_out, "timeout", {}, db
		)
		
		matchmaking_service.update_game_state(game_id, updated_gs)
		
		# Broadcast the events that resulted from the timeout
		for event in timeout_events:
			await game_manager._send_event(event, game_id)
			
		# After processing, check if a new timer needs to be started
		final_gs = matchmaking_service.get_full_game_state(game_id)
		if final_gs.status == "in_progress":
			next_player_id = final_gs.current_player_id
			# Start a new timer if the next player is human
			if next_player_id and not final_gs.players[next_player_id].is_bot:
				logger.info(f"G:{game_id} - Timeout processed. Starting new timer for human P:{next_player_id}.")
				_start_turn_timer(game_id, final_gs.turn_duration_seconds)
			# Or schedule a bot move if it's the bot's turn
			elif next_player_id and final_gs.players[next_player_id].is_bot:
				logger.info(f"G:{game_id} - Timeout processed. Scheduling new turn for bot P:{next_player_id}.")
				asyncio.create_task(handle_bot_turn(game_id, next_player_id))
	except Exception as e:
		logger.exception(f"Error in _handle_timeout for G:{game_id}: {e}")
	finally:
		if game_id in active_turn_timers: # Remove the completed/failed task
			del active_turn_timers[game_id]
		db.close() # Ensure DB session is closed


def _start_turn_timer(game_id: str, duration: int):
	"""Starts a new timer task for a game turn and stores it."""
	# First, cancel any existing timer for this game to be safe.
	if game_id in active_turn_timers and not active_turn_timers[game_id].done():
		logger.warning(f"G:{game_id} - Starting a new timer while an old one was still active. Cancelling old one.")
		active_turn_timers[game_id].cancel()

	logger.info(f"G:{game_id} - Starting turn timer for {duration} seconds.")
	# Create a new task that will call _handle_timeout after sleeping.
	timer_task = asyncio.create_task(asyncio.sleep(duration))
	# When the sleep is done, the callback will execute our timeout logic.
	timer_task.add_done_callback(
		lambda t: asyncio.create_task(_handle_timeout(game_id)) if not t.cancelled() else None
	)
	active_turn_timers[game_id] = timer_task


def _cancel_turn_timer(game_id: str):
	"""Cancels and removes the timer task for a game."""
	if game_id in active_turn_timers:
		task = active_turn_timers.pop(game_id) # Remove from dict
		if not task.done():
			task.cancel()
			logger.info(f"G:{game_id} - Turn timer cancelled.")


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
			logger.warning(f"G:{game_id} P:{user_id} - Reconnected, closing old websocket connection.")
			try:
				await self.active_connections[game_id][user_id].close(code=status.WS_1001_GOING_AWAY, reason="New connection established")
			except Exception as e:
				logger.exception(f"G:{game_id} P:{user_id} - Error closing old websocket: {e}")
		self.active_connections[game_id][user_id] = websocket
		logger.info(f"G:{game_id} P:{user_id} - WebSocket connected. Total players in this game's connection pool: {len(self.active_connections[game_id])}")

	def disconnect(self, game_id: str, user_id: int): # Use user_id (int)
		if game_id in self.active_connections and user_id in self.active_connections[game_id]:
			del self.active_connections[game_id][user_id]
			logger.info(f"G:{game_id} P:{user_id} - Removed from active connections manager.")
			if not self.active_connections[game_id]:
				logger.info(f"G:{game_id} - No players left, removing game from connection manager and triggering service cleanup.")
				del self.active_connections[game_id]
			
	async def _send_event(self, event: game_service.GameEvent, game_id_ctx: str):
		"""Helper to dispatch a GameEvent."""
		event_dict = event.to_dict()
		if event.broadcast:
			logger.debug(f"G:{game_id_ctx} - BROADCASTING event '{event.type}' (exclude: P:{event.exclude_player_id})")
			logger.debug(f"G:{game_id_ctx} - Event payload: {json.dumps(event_dict, indent=2)}")
			await self.broadcast_to_game(game_id_ctx, event_dict, exclude_player_id=event.exclude_player_id)
		elif event.target_player_id is not None:
			logger.debug(f"G:{game_id_ctx} - SENDING event '{event.type}' to P:{event.target_player_id}")
			logger.debug(f"G:{game_id_ctx} - Event payload: {json.dumps(event_dict, indent=2)}")
			await self.send_to_player(game_id_ctx, event.target_player_id, event_dict)
		else:
			logger.warning(f"G:{game_id_ctx} - Event '{event.type}' has no target and is not a broadcast. It will not be sent.")


	async def broadcast_to_game(self, game_id: str, message: dict, exclude_player_id: int | None = None): # Use exclude_player_id (int)
		if game_id in self.active_connections:
			tasks = []
			connections_to_send = list(self.active_connections[game_id].items())
			logger.debug(f"G:{game_id} - Broadcasting to players: {[pid for pid, _ in connections_to_send]}. Excluding: {exclude_player_id}")
			for player_id, connection in self.active_connections[game_id].items():
				if player_id != exclude_player_id:
					tasks.append(self._send_json_safe(connection, message, player_id, game_id))
			if tasks:
				await asyncio.gather(*tasks)

	async def send_to_player(self, game_id: str, user_id: int, message: dict): # Use user_id (int)
		if game_id in self.active_connections and user_id in self.active_connections[game_id]:
			connection = self.active_connections[game_id][user_id]
			await self._send_json_safe(connection, message, user_id, game_id) # Pass user_id

	async def _send_json_safe(self, connection: WebSocket, message: dict, user_id: int, game_id: str): # Use user_id (int)
		try:
			if connection.client_state == WebSocketState.CONNECTED:
				await connection.send_json(message)
			else: # Connection closed before sending
				logger.warning(f"G:{game_id} P:{user_id} - WS was already closed before sending '{message.get('type')}'. Disconnecting from manager.")
				self.disconnect(game_id, user_id) # Ensure manager cleanup
		except Exception as e:
			logger.exception(f"G:{game_id} P:{user_id} - Error sending message '{message.get('type')}': {e}. Disconnecting.")
			self.disconnect(game_id, user_id) # Ensure manager cleanup on send error
			# No need to broadcast player_disconnected here, as handle_player_disconnect will do it via game_service

game_manager = GameConnectionManager()

async def handle_bot_turn(game_id: str, bot_player_id: int):
	"""
	Handles the logic for a bot's turn, including dynamic delay.
	This function is called as a background task.
	"""
	
	db = SessionLocal()
	try:
		# Fetch the latest state to ensure the game is still valid for a bot move
		gs = matchmaking_service.get_full_game_state(game_id)
		if not gs or gs.status != "in_progress" or gs.current_player_id != bot_player_id:
			logger.info(f"G:{game_id} P:{bot_player_id} (Bot) - Turn aborted. Game state changed or ended (status: {gs.status if gs else 'N/A'}, current_player: {gs.current_player_id if gs else 'N/A'}).")
			return

		# Get the bot's move from the bot service
		logger.info(f"G:{game_id} P:{bot_player_id} (Bot) - Evaluating bot move.")
		bot_word, creativity_score = bot_service.get_bot_move(gs, db)

		# --- "Delay" Phase: Simulate thinking time based on creativity ---
		if bot_word:
			# Base delay of 1s, plus up to 3s for max creativity, plus random jitter
			delay = 1.0 + ((creativity_score - 1) * 0.75) + random.uniform(-0.5, 0.5)
			delay = max(0.5, min(delay, 4.0)) # Clamp delay between 0.5s and 4.0s
			logger.info(f"G:{game_id} P:{bot_player_id} (Bot) - Will play '{bot_word}' (creativity: {creativity_score}) after a {delay:.2f}s delay.")
			await asyncio.sleep(delay)
		else:
			# If bot "times out", make it happen after a longer, more realistic delay
			logger.info(f"G:{game_id} P:{bot_player_id} (Bot) - Is timing out. Waiting for a longer delay before processing.")
			await asyncio.sleep(random.uniform(4.0, 6.0))

		gs_after_delay = matchmaking_service.get_full_game_state(game_id)
		if not gs_after_delay or gs_after_delay.status != "in_progress":
			logger.info(f"G:{game_id} P:{bot_player_id} (Bot) - Turn aborted after delay. Game ended or status changed.")
			return
		
		action_type = "submit_word" if bot_word else "timeout"
		payload = {"word": bot_word} if bot_word else {}
		
		updated_gs_after_bot, bot_events = game_service.process_player_game_action(
			gs_after_delay,
			bot_player_id,
			action_type,
			payload,
			db
		)
		
		matchmaking_service.update_game_state(game_id, updated_gs_after_bot)
		
		# Broadcast the events resulting from the bot's turn
		for event in bot_events:
			await game_manager._send_event(event, game_id)
			
		# --- RECURSIVE CHECK: Is it the bot's turn again? ---
		# This can happen if the opponent makes a mistake and the turn passes back.
		if updated_gs_after_bot.status == "in_progress":
			next_player_id = updated_gs_after_bot.current_player_id
			if next_player_id and updated_gs_after_bot.players[next_player_id].is_bot:
				logger.info(f"G:{game_id} - Bot's turn again. Re-scheduling bot move for P:{next_player_id}.")
				asyncio.create_task(handle_bot_turn(game_id, next_player_id))
	except Exception as e:
		logger.exception(f"Error during bot turn for G:{game_id} P:{bot_player_id}: {e}")
	finally:
		db.close()
		

@router.websocket("/ws/game/{game_id}")
async def game_websocket_endpoint( # Renamed to avoid conflict with game_websocket var
	websocket: WebSocket,
	game_id: str,
	token: str = Query(..., description="User's JWT for authentication"), # Authenticate via JWT
	db: Session = Depends(deps.get_db)
):
	try:
		# Authenticate the user via the token
		# You might need to adapt get_current_user_from_backend_jwt or create a similar sync version
		# if it's called directly, or handle the async nature.
		# For simplicity, let's assume a helper that can be awaited:
		current_user = await deps.get_current_user_from_backend_jwt(token=token, db=db) # Pass db if needed by dep
		player_id_of_this_connection = current_user.id
		logger.info(f"G:{game_id} P:{player_id_of_this_connection} - WS Connection attempt received from user '{current_user.username}'.")
	except HTTPException as auth_exc:
		logger.error(f"G:{game_id} - WS Authentication failed: {auth_exc.detail}")
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Authentication failed: {auth_exc.detail}")
		return

	initial_game_state_from_matchmaking: Optional[GameState] = matchmaking_service.get_game_info(game_id)

	if not initial_game_state_from_matchmaking:
		logger.error(f"G:{game_id} P:{player_id_of_this_connection} - Player tried to connect to a game that does not exist in matchmaking_service. Closing WS.")
		await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game not found or already cleaned up")
		return

	# Validate player is part of this game
	# The player IDs are now stored in _temp_player_ids_ordered during "matched" state
	expected_player_ids = getattr(initial_game_state_from_matchmaking, 'matchmaking_player_order', []) \
						  if initial_game_state_from_matchmaking.status == "matched" \
						  else list(initial_game_state_from_matchmaking.players.keys())

	if player_id_of_this_connection not in expected_player_ids:
		logger.error(f"G:{game_id} P:{player_id_of_this_connection} - Player not authorized for this game. Expected players: {expected_player_ids}. Closing WS.")
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Player not authorized for this game")
		return

	await game_manager.connect(websocket, game_id, player_id_of_this_connection)
	
	events_to_send: List[game_service.GameEvent] = []
	current_game_state_model: Optional[GameState] = None # Will hold the authoritative GameState Pydantic model
	
	try:
		current_game_state_model = matchmaking_service.get_full_game_state(game_id)
		if not current_game_state_model: # Should not happen if get_game_info succeeded
			logger.error(f"G:{game_id} P:{player_id_of_this_connection} - CRITICAL: Game info disappeared after initial fetch. Closing WS.")
			await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game data inconsistency")
			return
		
		is_game_start_trigger = False
		if current_game_state_model.status == "matched" and not current_game_state_model.is_bot_game and len(game_manager.active_connections.get(game_id, {})) == 2:
			is_game_start_trigger = True
		# Case 2: Bot game, first (human) player connects
		elif current_game_state_model.status == "matched" and current_game_state_model.is_bot_game and len(game_manager.active_connections.get(game_id, {})) == 1:
			is_game_start_trigger = True


		if is_game_start_trigger:
			logger.info(f"G:{game_id} - Sufficient players connected. Initializing full game state (lang: {current_game_state_model.language}).")
			current_game_state_model, game_start_events = game_service.initialize_new_game_state(
				game_id, current_game_state_model, db
			)
			events_to_send.extend(game_start_events)
			matchmaking_service.update_game_state(game_id, current_game_state_model)
		
		elif current_game_state_model.status == "matched": # Not enough players yet
			logger.info(f"G:{game_id} P:{player_id_of_this_connection} - Connected to 'matched' game. Waiting for opponent.")
			status_event = game_service.GameEvent("info_message_to_player", {"message": "Waiting for opponent..."}, target_player_id=player_id_of_this_connection)
			events_to_send.append(status_event)

		elif current_game_state_model.status == "in_progress":
			logger.info(f"G:{game_id} P:{player_id_of_this_connection} - Reconnected to game 'in_progress'. Sending current state.")
			reconnect_event = game_service.prepare_reconnect_state_payload(game_id, current_game_state_model, player_id_of_this_connection)
			events_to_send.append(reconnect_event)
		
		elif current_game_state_model.status in ["finished", "abandoned_by_player", "error_content_load"]:
			logger.info(f"G:{game_id} P:{player_id_of_this_connection} - Connected to a terminal game (status: '{current_game_state_model.status}'). Sending final state.")
			final_event = game_service.prepare_reconnect_state_payload(game_id, current_game_state_model, player_id_of_this_connection)
			events_to_send.append(final_event)


		# Send any initial events (game_start, reconnect_state, status)
		for event in events_to_send:
			await game_manager._send_event(event, game_id)
		events_to_send.clear()


		current_game_state_model = matchmaking_service.get_full_game_state(game_id)
		if not current_game_state_model:
			logger.error(f"G:{game_id} - State unavailable after initial events. Closing P:{player_id_of_this_connection}")
			return
		
		# check if game starts with a bot's turn
		if current_game_state_model.status == "in_progress":
			current_player_id = current_game_state_model.current_player_id
			if current_player_id and current_game_state_model.players[current_player_id].is_bot:
				logger.info(f"G:{game_id} - Game starts with a bot's turn (P:{current_player_id}). Scheduling move.")
				asyncio.create_task(handle_bot_turn(game_id, current_player_id))
			elif current_player_id and not current_game_state_model.players[current_player_id].is_bot:
				# This is a new addition: start the timer for the human player when the game starts
				logger.info(f"G:{game_id} - Game starts with a human's turn (P:{current_player_id}). Starting timer.")
				_start_turn_timer(game_id, current_game_state_model.turn_duration_seconds)

		# Main loop for receiving player actions
		while websocket.client_state == WebSocketState.CONNECTED:
			current_game_state_model = matchmaking_service.get_full_game_state(game_id)
			
			if not current_game_state_model:
				logger.error(f"G:{game_id} P:{player_id_of_this_connection} - Game state disappeared during main loop. Breaking.")
				await game_manager.send_to_player(game_id, player_id_of_this_connection, {"type": "error", "message": "Game session ended unexpectedly."})
				break

			if current_game_state_model.status not in ["in_progress", "waiting_for_ready"]:
				logger.info(f"G:{game_id} P:{player_id_of_this_connection} - Game is in terminal state '{current_game_state_model.status}'. Ending WS loop.")
				break


			try:
				data = await websocket.receive_json()
				logger.debug(f"G:{game_id} P:{player_id_of_this_connection} - Received JSON: {data}")
			except WebSocketDisconnect: raise 
			except Exception as recv_e:
				logger.exception(f"G:{game_id} P:{player_id_of_this_connection} - Error receiving JSON: {recv_e}")
				break

			latest_gs_model = matchmaking_service.get_full_game_state(game_id)
			if not latest_gs_model:
				logger.error(f"G:{game_id} P:{player_id_of_this_connection} - State unavailable after receiving action. Game may have ended. Breaking loop.")
				try: await websocket.send_json({"type": "error", "payload": {"message": "Game session no longer available."}})
				except: pass
				break

			current_game_state_for_action  = latest_gs_model # Update our working copy

			action_type = data.get("action_type")
			action_payload_data = data.get("payload", {})

			if not action_type:
				logger.warning(f"G:{game_id} P:{player_id_of_this_connection} - Received message with no action_type. Ignoring.")
				continue

			_cancel_turn_timer(game_id)
			
			updated_game_state_model, resulting_events = game_service.process_player_game_action(
				current_game_state_for_action,
				player_id_of_this_connection,
				action_type,
				action_payload_data,
				db
			)
			
			matchmaking_service.update_game_state(game_id, updated_game_state_model) # Persist the updated Pydantic model
			logger.debug(f"G:{game_id} - State after processing action '{action_type}':\n{updated_game_state_model.model_dump_json(indent=2)}")

			for event_item in resulting_events:
				await game_manager._send_event(event_item, game_id)

			if updated_game_state_model.status == "in_progress":
				next_player_id = updated_game_state_model.current_player_id
				if next_player_id and not updated_game_state_model.players[next_player_id].is_bot:
					logger.info(f"G:{game_id} - Turn is now for human P:{next_player_id}. Starting their timer.")
					_start_turn_timer(game_id, updated_game_state_model.turn_duration_seconds)
				elif next_player_id and updated_game_state_model.players[next_player_id].is_bot:
					logger.info(f"G:{game_id} - Turn passed to bot P:{next_player_id}. Scheduling move.")
					asyncio.create_task(handle_bot_turn(game_id, next_player_id))
			
	except WebSocketDisconnect:
		logger.info(f"G:{game_id} P:{player_id_of_this_connection} - WebSocket Disconnected. Client state: {websocket.client_state}")
		
		# Cancel any running timer for this game when a player disconnects.
		_cancel_turn_timer(game_id)
		
		# Fetch current state before processing disconnect to avoid race conditions
		gs_on_dc = matchmaking_service.get_full_game_state(game_id)
		if gs_on_dc:
			logger.debug(f"G:{game_id} - State on disconnect for P:{player_id_of_this_connection}:\n{gs_on_dc.model_dump_json(indent=2)}")
			updated_gs_after_dc, dc_events = game_service.handle_player_disconnect(
				gs_on_dc, player_id_of_this_connection, db
			)
			if dc_events: 
				matchmaking_service.update_game_state(game_id, updated_gs_after_dc)
				for ev_dc in dc_events:
					await game_manager._send_event(ev_dc, game_id)
		else:
			logger.warning(f"G:{game_id} - State not found on P:{player_id_of_this_connection} disconnect. No further action taken.")
	
	except Exception as e:
		logger.exception(f"!!! G:{game_id} P:{player_id_of_this_connection} - UNEXPECTED ERROR in WS loop: {type(e).__name__} - {e} !!!")
		if websocket.client_state == WebSocketState.CONNECTED:
			try: await websocket.send_json({"type": "error", "payload": {"message": f"Internal server error: {type(e).__name__}"}})
			except Exception: pass
	
	finally:
		logger.debug(f"G:{game_id} P:{player_id_of_this_connection} - Entering WS 'finally' block. WS State: {websocket.client_state}")
		_cancel_turn_timer(game_id)
		
		if websocket.client_state != WebSocketState.DISCONNECTED:
			try:
				await websocket.close(code=status.WS_1001_GOING_AWAY)
				logger.info(f"G:{game_id} P:{player_id_of_this_connection} - Gracefully closed WS in 'finally' block.")
			except Exception as close_e: 
				logger.exception(f"G:{game_id} P:{player_id_of_this_connection} - Error closing WS in 'finally' block: {close_e}")
		
		game_manager.disconnect(game_id, player_id_of_this_connection)
		