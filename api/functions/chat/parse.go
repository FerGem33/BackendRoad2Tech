package main

import (
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"golang.org/x/text/runes"
	"golang.org/x/text/transform"
	"golang.org/x/text/unicode/norm"
)

type intent string

const (
	intentApprove  intent = "approve"
	intentPromo    intent = "promo"
	intentRestock  intent = "restock"
	intentForecast intent = "forecast"
	intentContext  intent = "context"
)

// parsed is what the orchestrator understood from the message (interpreted_constraints in the response).
type parsed struct {
	Intent         intent
	ProductIDs     []string
	CategoryID     string
	Weekday        string // English, as in the dataset (Saturday)
	MaxDiscountPct *float64
}

func normalize(s string) string {
	t := transform.Chain(norm.NFD, runes.Remove(runes.In(unicode.Mn)), norm.NFC)
	out, _, _ := transform.String(t, strings.ToLower(s))
	return out
}

var (
	reDiscount = regexp.MustCompile(`(\d{1,2}(?:[.,]\d+)?)\s*(?:%|por ?ciento)`)
	weekdays   = map[string]string{"lunes": "Monday", "martes": "Tuesday", "miercoles": "Wednesday", "jueves": "Thursday",
		"viernes": "Friday", "sabado": "Saturday", "domingo": "Sunday"}
	// Spanish keywords -> category_id (categories.csv)
	categoryWords = []struct {
		id    string
		words []string
	}{
		{"C02", []string{"cerveza", "alcohol", "tequila", "ron ", "licor", "vino"}},
		{"C01", []string{"bebida", "refresco", "agua", "jugo", "soda", "energetic", "electrolit", "hidrat"}},
		{"C03", []string{"botana", "papitas", "frituras", "snack"}},
		{"C04", []string{"dulce", "chocolate", "golosina", "chicle"}},
		{"C05", []string{"comida preparada", "sandwich", "torta", "hot dog", "preparad"}},
		{"C06", []string{"cafe", "bebida caliente"}},
		{"C07", []string{"lacteo", "leche", "yogur", "refrigerad", "queso"}},
		{"C08", []string{"pan ", "panaderia", "galleta"}},
		{"C09", []string{"abarrote"}},
		{"C10", []string{"helado", "congelad", "paleta"}},
		{"C11", []string{"hielo"}},
		{"C12", []string{"cigarro", "tabaco"}},
		{"C13", []string{"higiene", "shampoo", "jabon", "desodorante"}},
		{"C14", []string{"limpieza", "detergente", "cloro"}},
		{"C15", []string{"farmacia", "medicina", "medicamento"}},
		{"C16", []string{"mascota", "perro", "gato"}},
		{"C17", []string{"bebe", "panal"}},
		{"C18", []string{"fruta", "verdura"}},
		{"C19", []string{"desechable", "fiesta", "vasos", "platos"}},
		{"C20", []string{"flor", "regalo"}},
	}
	intentWords = []struct {
		intent intent
		words  []string
	}{
		{intentApprove, []string{"activa", "aprueba", "aprobar", "autoriza", "programa la"}},
		{intentPromo, []string{"descuento", "promo", "oferta", "aumenta", "aumentar", "impulsa", "vender mas", "combo",
			"incrementa", "estrategia", "liquidar", "rescat", "rebaja"}},
		{intentRestock, []string{"reabast", "surtir", "surte", "pedir", "pedido", "orden de compra", "inventario", "stock",
			"agot", "quiebre", "proveedor"}},
		{intentForecast, []string{"pronost", "tendencia", "proyecc", "cuanto se va a vender", "demanda", "forecast", "ventas"}},
	}
)

func parse(message string, products []db.Product) parsed {
	m := " " + normalize(message) + " "
	p := parsed{Intent: intentContext}
	for _, iw := range intentWords {
		if containsAny(m, iw.words) {
			p.Intent = iw.intent
			break
		}
	}
	if g := reDiscount.FindStringSubmatch(m); g != nil {
		if v, err := strconv.ParseFloat(strings.Replace(g[1], ",", ".", 1), 64); err == nil && v > 0 && v < 100 {
			f := v / 100
			p.MaxDiscountPct = &f
		}
	}
	for es, en := range weekdays {
		if strings.Contains(m, es) {
			p.Weekday = en
			break
		}
	}
	for _, cw := range categoryWords {
		if containsAny(m, cw.words) {
			p.CategoryID = cw.id
			break
		}
	}
	energy := strings.Contains(m, "energetic") || strings.Contains(m, "energy")
	for _, pr := range products {
		name, brand := normalize(pr.ProductName), normalize(pr.Brand)
		if (energy && strings.Contains(name, "energy")) || strings.Contains(m, " "+name+" ") ||
			(len(brand) >= 4 && strings.Contains(m, brand)) {
			p.ProductIDs = append(p.ProductIDs, pr.ProductID)
		}
	}
	return p
}

func containsAny(s string, words []string) bool {
	for _, w := range words {
		if strings.Contains(s, w) {
			return true
		}
	}
	return false
}
