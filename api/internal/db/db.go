// Package db is the access layer for the single table `road2tech-stores` (docs/DYNAMO.md).
package db

import (
	"context"
	"errors"
	"os"
	"sync"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

type AV = types.AttributeValue

var (
	once   sync.Once
	client *dynamodb.Client
)

func Client(ctx context.Context) *dynamodb.Client {
	once.Do(func() {
		cfg, err := config.LoadDefaultConfig(ctx)
		if err != nil {
			panic(err)
		}
		client = dynamodb.NewFromConfig(cfg)
	})
	return client
}

func Table() string {
	if t := os.Getenv("TABLE_NAME"); t != "" {
		return t
	}
	return "road2tech-stores"
}

func S(v string) *types.AttributeValueMemberS { return &types.AttributeValueMemberS{Value: v} }

func Key(pk, sk string) map[string]AV { return map[string]AV{"PK": S(pk), "SK": S(sk)} }

// Get reads one item into out. Returns (false, nil) when the item does not exist.
func Get(ctx context.Context, pk, sk string, out any) (bool, error) {
	res, err := Client(ctx).GetItem(ctx, &dynamodb.GetItemInput{TableName: aws.String(Table()), Key: Key(pk, sk)})
	if err != nil {
		return false, err
	}
	if res.Item == nil {
		return false, nil
	}
	return true, attributevalue.UnmarshalMap(res.Item, out)
}

// Q describes a key-condition query; Index is "" for the base table.
type Q struct {
	Index   string
	PKName  string // PK, GSI1PK or GSI2PK
	SKName  string
	PK      string
	Begins  string // SK begins_with
	From    string // SK BETWEEN From AND To
	To      string
	Reverse bool
}

func (q Q) input() *dynamodb.QueryInput {
	pkName, skName := q.PKName, q.SKName
	if pkName == "" {
		pkName, skName = "PK", "SK"
		switch q.Index {
		case "GSI1":
			pkName, skName = "GSI1PK", "GSI1SK"
		case "GSI2":
			pkName, skName = "GSI2PK", "GSI2SK"
		}
	}
	in := &dynamodb.QueryInput{
		TableName:                 aws.String(Table()),
		KeyConditionExpression:    aws.String("#pk = :pk"),
		ExpressionAttributeNames:  map[string]string{"#pk": pkName},
		ExpressionAttributeValues: map[string]AV{":pk": S(q.PK)},
		ScanIndexForward:          aws.Bool(!q.Reverse),
	}
	if q.Index != "" {
		in.IndexName = aws.String(q.Index)
	}
	switch {
	case q.Begins != "":
		in.KeyConditionExpression = aws.String("#pk = :pk AND begins_with(#sk, :b)")
		in.ExpressionAttributeNames["#sk"] = skName
		in.ExpressionAttributeValues[":b"] = S(q.Begins)
	case q.From != "" || q.To != "":
		in.KeyConditionExpression = aws.String("#pk = :pk AND #sk BETWEEN :f AND :t")
		in.ExpressionAttributeNames["#sk"] = skName
		in.ExpressionAttributeValues[":f"] = S(q.From)
		in.ExpressionAttributeValues[":t"] = S(q.To)
	}
	return in
}

// QueryAll runs the query through every page and unmarshals all items into out (a pointer to a slice).
func QueryAll(ctx context.Context, q Q, out any) error {
	var items []map[string]AV
	p := dynamodb.NewQueryPaginator(Client(ctx), q.input())
	for p.HasMorePages() {
		page, err := p.NextPage(ctx)
		if err != nil {
			return err
		}
		items = append(items, page.Items...)
	}
	return attributevalue.UnmarshalListOfMaps(items, out)
}

// Put writes an item. cond is an optional ConditionExpression.
func Put(ctx context.Context, item any, cond string) error {
	m, err := attributevalue.MarshalMap(item)
	if err != nil {
		return err
	}
	in := &dynamodb.PutItemInput{TableName: aws.String(Table()), Item: m}
	if cond != "" {
		in.ConditionExpression = aws.String(cond)
	}
	_, err = Client(ctx).PutItem(ctx, in)
	return err
}

func Delete(ctx context.Context, pk, sk string) error {
	_, err := Client(ctx).DeleteItem(ctx, &dynamodb.DeleteItemInput{TableName: aws.String(Table()), Key: Key(pk, sk)})
	return err
}

func IsConditionFailed(err error) bool {
	var c *types.ConditionalCheckFailedException
	var t *types.TransactionCanceledException
	return errors.As(err, &c) || errors.As(err, &t)
}

// NextCounter atomically increments COUNTER/<name> and returns the new value.
func NextCounter(ctx context.Context, name string) (int64, error) {
	res, err := Client(ctx).UpdateItem(ctx, &dynamodb.UpdateItemInput{
		TableName:                 aws.String(Table()),
		Key:                       Key("COUNTER", name),
		UpdateExpression:          aws.String("ADD #v :one SET entity_type = :t"),
		ExpressionAttributeNames:  map[string]string{"#v": "value"},
		ExpressionAttributeValues: map[string]AV{":one": &types.AttributeValueMemberN{Value: "1"}, ":t": S("COUNTER")},
		ReturnValues:              types.ReturnValueUpdatedNew,
	})
	if err != nil {
		return 0, err
	}
	var out struct {
		Value int64 `dynamodbav:"value"`
	}
	err = attributevalue.UnmarshalMap(res.Attributes, &out)
	return out.Value, err
}
