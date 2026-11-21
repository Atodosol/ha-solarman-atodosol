from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BoardState
from .coordinator import BoardCoordinator


class BoardEntity(CoordinatorEntity[BoardCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BoardCoordinator, key: str):
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def board(self) -> BoardState:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and self.available_on(self.coordinator.data)

    def available_on(self, state: BoardState) -> bool:
        return True


class BoardControl(BoardEntity):
    """Settings: only while the board accepts changes (`writable`).

    When it does not, the «Ajustes» sensor says why (`notice`).
    """

    def available_on(self, state: BoardState) -> bool:
        return state.writable and self.control_available(state)

    def control_available(self, state: BoardState) -> bool:
        return True
