package main

import (
	"testing"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
)

func TestParseInventoryQuestions(t *testing.T) {
	products := []db.Product{{ProductID: "P001", ProductName: "Coca-Cola 600 ml", Brand: "Coca-Cola"}}
	tests := []string{
		"¿Tengo stock suficiente de Coca-Cola 600 ml?",
		"¿Se agotará este producto o quedará merma al final del mes?",
		"¿Cuál es la cobertura de inventario?",
	}
	for _, message := range tests {
		t.Run(message, func(t *testing.T) {
			if got := parse(message, products).Intent; got != intentInventory {
				t.Fatalf("parse(%q).Intent = %q, want %q", message, got, intentInventory)
			}
		})
	}
}

func TestParseRestockStillUsesRestockIntent(t *testing.T) {
	if got := parse("¿Qué debo pedir esta semana?", nil).Intent; got != intentRestock {
		t.Fatalf("restock question intent = %q, want %q", got, intentRestock)
	}
}
