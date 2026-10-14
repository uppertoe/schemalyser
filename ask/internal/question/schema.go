package question

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// The validator of the question schema's subset of JSON Schema: type, enum, const, properties, required,
// additionalProperties, items, minItems, maxItems, minLength, pattern, minimum, maximum, anyOf and $ref to #/$defs.
// The core's reader implements the same subset in Python, so that the one schema beside the contract is read on both
// sides and a package the service saves is one the core accepts.

// Validate decodes a package's bytes and gives the problems against the schema, each as "path: sentence", or none.
func Validate(data []byte, schema []byte) ([]string, error) {
	var root map[string]any
	if err := json.Unmarshal(schema, &root); err != nil {
		return nil, fmt.Errorf("the schema is not readable: %w", err)
	}
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		return nil, fmt.Errorf("the package is not JSON: %w", err)
	}
	return validate(value, root, "", root), nil
}

func validate(value any, schema map[string]any, path string, root map[string]any) []string {
	var problems []string
	where := path
	if where == "" {
		where = "the package"
	}
	if ref, ok := schema["$ref"].(string); ok {
		name := ref[strings.LastIndex(ref, "/")+1:]
		defs, _ := root["$defs"].(map[string]any)
		target, _ := defs[name].(map[string]any)
		return validate(value, target, path, root)
	}
	if options, ok := schema["anyOf"].([]any); ok {
		for _, option := range options {
			if o, ok := option.(map[string]any); ok && len(validate(value, o, path, root)) == 0 {
				return nil
			}
		}
		return []string{where + ": is none of the shapes it may have"}
	}
	if want, ok := schema["const"]; ok {
		if !equal(value, want) {
			return []string{fmt.Sprintf("%s: is not %s", where, text(want))}
		}
	}
	if options, ok := schema["enum"].([]any); ok {
		found := false
		for _, o := range options {
			if equal(value, o) {
				found = true
			}
		}
		if !found {
			words := make([]string, 0, len(options))
			for _, o := range options {
				words = append(words, text(o))
			}
			return []string{fmt.Sprintf("%s: is not one of %s", where, strings.Join(words, ", "))}
		}
	}
	if t, ok := schema["type"]; ok && !ofType(value, t) {
		return []string{fmt.Sprintf("%s: is not %s", where, an(typeWords(t)))}
	}
	switch v := value.(type) {
	case string:
		if min, ok := number(schema["minLength"]); ok && float64(len([]rune(v))) < min {
			if min == 1 {
				problems = append(problems, path+": is empty")
			} else {
				problems = append(problems, fmt.Sprintf("%s: is shorter than %v characters", path, min))
			}
		}
		if pattern, ok := schema["pattern"].(string); ok {
			if re, err := regexp.Compile(pattern); err == nil && !re.MatchString(v) {
				problems = append(problems, fmt.Sprintf("%s: does not match %s", path, pattern))
			}
		}
	case json.Number:
		f, _ := v.Float64()
		if min, ok := number(schema["minimum"]); ok && f < min {
			problems = append(problems, fmt.Sprintf("%s: is below %v", path, min))
		}
		if max, ok := number(schema["maximum"]); ok && f > max {
			problems = append(problems, fmt.Sprintf("%s: is above %v", path, max))
		}
	case []any:
		if min, ok := number(schema["minItems"]); ok && float64(len(v)) < min {
			if min == 1 {
				problems = append(problems, path+": is empty")
			} else {
				problems = append(problems, fmt.Sprintf("%s: holds fewer than %v items", path, min))
			}
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(v)) > max {
			problems = append(problems, fmt.Sprintf("%s: holds more than %v items", path, max))
		}
		if items, ok := schema["items"].(map[string]any); ok {
			for n, item := range v {
				problems = append(problems, validate(item, items, fmt.Sprintf("%s[%d]", path, n), root)...)
			}
		}
	case map[string]any:
		if required, ok := schema["required"].([]any); ok {
			for _, field := range required {
				name, _ := field.(string)
				if _, held := v[name]; !held {
					problems = append(problems, join(path, name)+": is missing")
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, field := range keys {
			child := join(path, field)
			if p, ok := properties[field].(map[string]any); ok {
				problems = append(problems, validate(v[field], p, child, root)...)
			} else if extra, ok := schema["additionalProperties"]; ok {
				if allowed, isBool := extra.(bool); isBool && !allowed {
					if path == "" {
						problems = append(problems, child+": is not a field of the package")
					} else {
						problems = append(problems, child+": is not a field it may have")
					}
				} else if p, ok := extra.(map[string]any); ok {
					problems = append(problems, validate(v[field], p, child, root)...)
				}
			}
		}
	}
	return problems
}

func join(path, field string) string {
	if path == "" {
		return field
	}
	return path + "." + field
}

func number(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

func ofType(value any, want any) bool {
	names, ok := want.([]any)
	if !ok {
		names = []any{want}
	}
	for _, n := range names {
		name, _ := n.(string)
		switch name {
		case "integer":
			if num, ok := value.(json.Number); ok && !strings.ContainsAny(num.String(), ".eE") {
				return true
			}
		case "number":
			if _, ok := value.(json.Number); ok {
				return true
			}
		case "string":
			if _, ok := value.(string); ok {
				return true
			}
		case "boolean":
			if _, ok := value.(bool); ok {
				return true
			}
		case "array":
			if _, ok := value.([]any); ok {
				return true
			}
		case "object":
			if _, ok := value.(map[string]any); ok {
				return true
			}
		case "null":
			if value == nil {
				return true
			}
		}
	}
	return false
}

func typeWords(want any) string {
	if names, ok := want.([]any); ok {
		words := make([]string, 0, len(names))
		for _, n := range names {
			words = append(words, fmt.Sprint(n))
		}
		return strings.Join(words, " or ")
	}
	return fmt.Sprint(want)
}

func an(word string) string {
	if strings.ContainsRune("aeiou", rune(word[0])) {
		return "an " + word
	}
	return "a " + word
}

func equal(value, want any) bool {
	if num, ok := value.(json.Number); ok {
		if f, ok := number(want); ok {
			v, _ := num.Float64()
			return v == f
		}
		return false
	}
	return value == want
}

func text(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
