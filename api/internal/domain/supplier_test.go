package domain

import (
	"encoding/csv"
	"os"
	"strconv"
	"testing"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
)

const v2 = "../../../public/softtek/dataset/dataset-hackathon-road2tech/v2/"

func readCSV(t *testing.T, name string) []map[string]string {
	f, err := os.Open(v2 + name + ".csv")
	if err != nil {
		t.Skip("dataset not available:", err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	out := make([]map[string]string, 0, len(rows)-1)
	for _, r := range rows[1:] {
		m := map[string]string{}
		for i, h := range rows[0] {
			m[h] = r[i]
		}
		out = append(out, m)
	}
	return out
}

func num(s string) float64 { v, _ := strconv.ParseFloat(s, 64); return v }

// Expected values come from scripts/dataset/supplier_selection.decide().
func TestDecideMatchesPython(t *testing.T) {
	rel := map[string]float64{}
	for _, s := range readCSV(t, "suppliers") {
		rel[s["supplier_id"]] = num(s["reliability_score"])
	}
	offers := map[string][]db.Offer{}
	for _, o := range readCSV(t, "supplier_products") {
		offers[o["product_id"]] = append(offers[o["product_id"]], db.Offer{SupplierID: o["supplier_id"],
			UnitCost: num(o["unit_cost"]), MinOrderQty: int(num(o["min_order_qty"])),
			LeadTimeDays: int(num(o["lead_time_days"])), ReliabilityScore: rel[o["supplier_id"]]})
	}
	prods := map[string]map[string]string{}
	for _, p := range readCSV(t, "products") {
		prods[p["product_id"]] = p
	}
	cases := []struct {
		pid           string
		demand, stock float64
		sup           string
		qty           int
		urgent        bool
	}{
		{"P001", 30, 50, "SUP12", 270, true},
		{"P001", 30, 0, "SUP12", 270, true},
		{"P059", 4, 3, "SUP09", 28, true},
		{"P032", 0.05, 1, "SUP14", 4, false},
		{"P106", 0.2467, 1, "SUP05", 12, false},
		{"P090", 12, 100, "SUP10", 132, false},
	}
	for _, c := range cases {
		p := prods[c.pid]
		d := Decide(offers[c.pid], num(p["retail_price"]), int(num(p["shelf_life_days"])), c.demand, c.stock)
		if d.Offer.SupplierID != c.sup || d.Quantity != c.qty || d.IsUrgent != c.urgent {
			t.Errorf("%s demand=%v stock=%v: got %s %d %v, want %s %d %v", c.pid, c.demand, c.stock,
				d.Offer.SupplierID, d.Quantity, d.IsUrgent, c.sup, c.qty, c.urgent)
		}
	}
}
