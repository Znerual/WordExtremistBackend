	  
# app/api/websockets.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status, Query
from starlette.websockets import WebSocketState
from sqlalchemy.orm import Session
from typing import Dict, List
from app.api import deps
from app.models.user import UserPublic
from app.services import matchmaking_service, game_service
from app.models.game import PlayerAction, GameState, GameStatePlayer, SentencePromptPublic # Pydantic models
from app.crud import crud_game_content # Keep for fetching sentence prompts
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

	async def _send_event(self, event: game_service.GameEvent, game_id_ctx: str):
		"""Helper to dispatch a GameEvent."""
		if event.broadcast:
			# print(f"Broadcasting event G:{game_id_ctx} Type:{event.type} Exclude:{event.exclude_player_id}")
			await self.broadcast_to_game(game_id_ctx, event.to_dict(), exclude_player_id=event.exclude_player_id)
		elif event.target_player_id is not None:
			# print(f"Sending event to P:{event.target_player_id} G:{game_id_ctx} Type:{event.type}")
			await self.send_to_player(game_id_ctx, event.target_player_id, event.to_dict())
		# If target_player_id is None and not broadcast, it might be an event for the acting player,
		# which should have been specified as target_player_id by game_service.

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
async def game_websocket_endpoint( # Renamed to avoid conflict with game_websocket var
	websocket: WebSocket,
	game_id: str,
	user_id: int = Query(..., description="User ID matching the one used in matchmaking"),
	db: Session = Depends(deps.get_db)
):
	player_id_of_this_connection = user_id
	print(f"Connection attempt: P:{player_id_of_this_connection} G:{game_id}")

	matchmaking_game_info = matchmaking_service.get_game_info(game_id)
	if not matchmaking_game_info:
		print(f"G:{game_id} not found in matchmaking. Closing WS for P:{player_id_of_this_connection}.")
		await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game not found")
		return
	if player_id_of_this_connection not in matchmaking_game_info.get("players", []):
		print(f"P:{player_id_of_this_connection} not in G:{game_id} players: {matchmaking_game_info.get('players', [])}. Closing WS.")
		await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Player not in game")
		return

	await game_manager.connect(websocket, game_id, player_id_of_this_connection)
	
	events_to_send: List[game_service.GameEvent] = []
	try:
		current_game_state_dict = matchmaking_service.get_full_game_state(game_id)

		if not current_game_state_dict or current_game_state_dict.get("status") == "matched":
			if len(game_manager.active_connections.get(game_id, {})) == 2:
				# Both players connected, time to initialize the game state fully
				# Re-fetch state to avoid race conditions if another connection initialized it.
				current_game_state_dict = matchmaking_service.get_full_game_state(game_id)
				if not current_game_state_dict or current_game_state_dict.get("status") != "in_progress":
					print(f"G:{game_id} both players connected. Initializing game state.")
					initial_sentence = crud_game_content.get_random_sentence_prompt(db)
					if not initial_sentence:
						err_event = game_service.GameEvent(
							"error_message_broadcast", 
							{"message": "Failed to load game content."}, 
							broadcast=True
						)
						await game_manager._send_event(err_event, game_id)
						# Close connections as game cannot start
						for pid_close in list(game_manager.active_connections.get(game_id,{}).keys()):
							ws_close = game_manager.active_connections.get(game_id,{}).get(pid_close)
							if ws_close and ws_close.client_state != WebSocketState.DISCONNECTED:
								await ws_close.close(code=status.WS_1011_INTERNAL_ERROR, reason="Game content error")
						matchmaking_service.cleanup_game(game_id) # Remove game if content fails
						return

					current_game_state_dict, game_start_events = game_service.initialize_new_game_state(
						game_id, matchmaking_game_info, SentencePromptPublic.model_validate(initial_sentence), db
					)
					matchmaking_service.update_game_state(game_id, current_game_state_dict) # Persist initialized state
					events_to_send.extend(game_start_events)
				else: # Game was already initialized by the other player's connection
					print(f"G:{game_id} already in_progress. P:{player_id_of_this_connection} is reconnecting/joining.")
					reconnect_event = game_service.prepare_reconnect_state_payload(game_id, current_game_state_dict, player_id_of_this_connection)
					events_to_send.append(reconnect_event)

			else: # Only one player connected to a "matched" game
				status_event = game_service.GameEvent("status", {"message": "Waiting for opponent..."}, target_player_id=player_id_of_this_connection)
				events_to_send.append(status_event)
		
		elif current_game_state_dict.get("status") == "in_progress": # Reconnecting to an active game
			print(f"P:{player_id_of_this_connection} reconnected to G:{game_id} (in_progress). Sending state.")
			reconnect_event = game_service.prepare_reconnect_state_payload(game_id, current_game_state_dict, player_id_of_this_connection)
			events_to_send.append(reconnect_event)
		
		elif current_game_state_dict.get("status") == "finished" or current_game_state_dict.get("status", "").startswith("error"):
			print(f"P:{player_id_of_this_connection} connected to G:{game_id} but status is '{current_game_state_dict.get('status')}'. Sending game over/error.")
			# Send a game_over or error message if game is already concluded or errored
			# This needs specific logic based on how you want to handle reconnects to finished games.
			# For now, just send a generic message.
			final_status_payload = {"message": f"Game already ended with status: {current_game_state_dict.get('status')}."}
			if current_game_state_dict.get("status") == "finished":
				p1_final_id = current_game_state_dict["matchmaking_player_order"][0]
				p2_final_id = current_game_state_dict["matchmaking_player_order"][1]
				final_status_payload = { # Mimic game_over payload structure
					"game_winner_id": str(current_game_state_dict.get("game_winner_id")) if current_game_state_dict.get("game_winner_id") else None, # Assuming winner is stored
					"player1_server_id": str(p1_final_id), 
					"player2_server_id": str(p2_final_id),
					"player1_final_score": current_game_state_dict["players"][p1_final_id]["score"], 
					"player2_final_score": current_game_state_dict["players"][p2_final_id]["score"],
				}
				events_to_send.append(game_service.GameEvent("game_over", final_status_payload, target_player_id=player_id_of_this_connection))

			else: # Error state
				events_to_send.append(game_service.GameEvent("error_message_to_player", final_status_payload, target_player_id=player_id_of_this_connection))


		# Send any initial events (game_start, reconnect_state, status)
		for event in events_to_send:
			await game_manager._send_event(event, game_id)
		events_to_send.clear()


		# Main loop for receiving player actions
		while True:
			if websocket.client_state != WebSocketState.CONNECTED:
				print(f"P:{player_id_of_this_connection} G:{game_id} - WebSocket no longer connected in main loop. Breaking.")
				break # Exit loop if connection is no longer valid

			data = await websocket.receive_json()
			action_type = data.get("action_type")
			action_payload_data = data.get("payload", {})
			
			# print(f"Action from P:{player_id_of_this_connection} G:{game_id}: {action_type}")

			# Always fetch the latest state before processing any action
			current_game_state_dict_rt = matchmaking_service.get_full_game_state(game_id)
			if not current_game_state_dict_rt or current_game_state_dict_rt.get("status") != "in_progress":
				print(f"G:{game_id} not 'in_progress' (is {current_game_state_dict_rt.get('status')}). Ignoring action '{action_type}' from P:{player_id_of_this_connection}.")
				# Optionally send an error to the client that the game is over / not active
				if current_game_state_dict_rt and current_game_state_dict_rt.get("status") == "finished":
					p1f_id = current_game_state_dict_rt["matchmaking_player_order"][0]
					p2f_id = current_game_state_dict_rt["matchmaking_player_order"][1]
					game_over_payload_rt = {
						"game_winner_id": str(current_game_state_dict_rt.get("game_winner_id")) if current_game_state_dict_rt.get("game_winner_id") else None,
						"player1_server_id": str(p1f_id), "player2_server_id": str(p2f_id),
						"player1_final_score": current_game_state_dict_rt["players"][p1f_id]["score"], 
						"player2_final_score": current_game_state_dict_rt["players"][p2f_id]["score"],
					}
					await game_manager.send_to_player(game_id, player_id_of_this_connection, game_service.GameEvent("game_over", game_over_payload_rt).to_dict())
				else:
					await game_manager.send_to_player(game_id, player_id_of_this_connection, {"type": "error", "message": "Game is not currently active or has ended."})
				continue 

			# Process action using game_service
			updated_game_state, resulting_events = game_service.process_player_game_action(
				current_game_state_dict_rt, # Pass the mutable dict
				player_id_of_this_connection,
				action_type,
				action_payload_data,
				db
			)
			
			# Persist the state potentially modified by game_service
			matchmaking_service.update_game_state(game_id, updated_game_state)
			
			# Send out all events generated by the action
			for event_item in resulting_events:
				await game_manager._send_event(event_item, game_id)
			
			# If game status changed to finished or error, break loop (client will be informed by event)
			if updated_game_state.get("status") != "in_progress":
				print(f"G:{game_id} status changed to '{updated_game_state.get('status')}' after action. P:{player_id_of_this_connection} WS loop might end.")
				# Consider if we should explicitly break or wait for client to disconnect.
				# If a "game_over" or "error_message_broadcast" (that implies game end) was sent,
				# the client should handle it. Loop can continue to listen for potential stray messages or disconnect.
				# However, if game content load error, game should end.
				if updated_game_state.get("status") == "error_content_load":
					print(f"G:{game_id} Content load error. Breaking WS loop for P:{player_id_of_this_connection}")
					break


	except WebSocketDisconnect:
		print(f"WS Disconnected: P:{player_id_of_this_connection} G:{game_id}. Client state: {websocket.client_state}")
		# Game service handles disconnect logic and generates events
		current_gs_on_dc = matchmaking_service.get_full_game_state(game_id)
		if current_gs_on_dc: # Only process if game state exists
			updated_gs_after_dc, dc_events = game_service.handle_player_disconnect(
				current_gs_on_dc, player_id_of_this_connection, db
			)
			if dc_events: # Only update and send if there were consequential events
				matchmaking_service.update_game_state(game_id, updated_gs_after_dc)
				for ev_dc in dc_events:
					await game_manager._send_event(ev_dc, game_id)
		else:
			print(f"G:{game_id} state not found on P:{player_id_of_this_connection} disconnect. No further action.")
		# disconnect from manager handled in finally
	
	except Exception as e:
		print(f"!!! UNEXPECTED ERROR in WS G:{game_id} P:{player_id_of_this_connection}: {type(e).__name__} - {e} !!!")
		import traceback
		traceback.print_exc()
		# Attempt to inform client of server error if possible
		if websocket.client_state == WebSocketState.CONNECTED:
			try:
				await websocket.send_json({"type": "error", "payload": {"message": f"Internal server error: {type(e).__name__}"}})
			except Exception: pass # Best effort
		# disconnect from manager handled in finally

	finally:
		print(f"Finally block for P:{player_id_of_this_connection} G:{game_id}. WS State: {websocket.client_state}")
		# Ensure graceful close if not already closed by FastAPI/uvicorn or an explicit error
		if websocket.client_state != WebSocketState.DISCONNECTED:
			try:
				await websocket.close(code=status.WS_1001_GOING_AWAY)
				print(f"Gracefully closed WS for P:{player_id_of_this_connection} G:{game_id} in finally.")
			except RuntimeError as re: # Can happen if already closing
				print(f"RuntimeError closing WS for P:{player_id_of_this_connection} G:{game_id} in finally: {re} (likely already closing/closed)")
			except Exception as close_e:
				print(f"Error closing WS for P:{player_id_of_this_connection} G:{game_id} in finally: {close_e}")
		
		game_manager.disconnect(game_id, player_id_of_this_connection)
		print(f"Exited WS handler for P:{player_id_of_this_connection} G:{game_id}. Remaining conns for G:{game_id}: {list(game_manager.active_connections.get(game_id, {}).keys())}")