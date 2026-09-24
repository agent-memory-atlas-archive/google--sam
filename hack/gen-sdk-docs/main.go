// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Command gen-sdk-docs copies the SDK example programs into the Markdown
// that shows them, so what the READMEs and the guide print is the file CI
// compiles and runs against a mesh. A Markdown file marks where a file goes
// with a path relative to the repository root:
//
//	<!-- embed: sdk/js/examples/call.ts -->
//	```ts
//	(replaced with the file)
//	```
//	<!-- /embed -->
//
// Usage: gen-sdk-docs [-check] <markdown file or directory>...
//
// Directories are walked for .md files. With -check nothing is written and
// the exit status is 1 when any file is stale; hack/verify-sdk-generated.sh
// runs it that way.
package main

import (
	"bytes"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var embedBlock = regexp.MustCompile("(?s)<!-- embed: (\\S+) -->\n.*?<!-- /embed -->")

var fenceLanguages = map[string]string{
	".ts":   "ts",
	".js":   "js",
	".py":   "python",
	".go":   "go",
	".sh":   "bash",
	".yaml": "yaml",
	".json": "json",
}

func main() {
	check := false
	var paths []string
	for _, arg := range os.Args[1:] {
		if arg == "-check" {
			check = true
			continue
		}
		paths = append(paths, arg)
	}
	if len(paths) == 0 {
		fmt.Fprintln(os.Stderr, "usage: gen-sdk-docs [-check] <markdown file or directory>...")
		os.Exit(2)
	}
	files, err := markdownFiles(paths)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	stale := 0
	for _, file := range files {
		changed, err := render(file, !check)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", file, err)
			os.Exit(1)
		}
		if changed {
			stale++
			if check {
				fmt.Fprintf(os.Stderr, "%s: embedded examples are stale\n", file)
			} else {
				fmt.Printf("%s: updated\n", file)
			}
		}
	}
	if check && stale > 0 {
		fmt.Fprintln(os.Stderr, "Run: go run ./hack/gen-sdk-docs sdk site/content/docs")
		os.Exit(1)
	}
}

func markdownFiles(paths []string) ([]string, error) {
	var files []string
	for _, p := range paths {
		info, err := os.Stat(p)
		if err != nil {
			return nil, err
		}
		if !info.IsDir() {
			files = append(files, p)
			continue
		}
		err = filepath.WalkDir(p, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() && (d.Name() == "node_modules" || d.Name() == ".venv" || d.Name() == "dist" || d.Name() == "build") {
				return filepath.SkipDir
			}
			if !d.IsDir() && strings.HasSuffix(path, ".md") {
				files = append(files, path)
			}
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return files, nil
}

// render rewrites every embed block in a Markdown file from the file it
// names and reports whether the result differs from what is on disk.
func render(file string, write bool) (bool, error) {
	before, err := os.ReadFile(file)
	if err != nil {
		return false, err
	}
	var renderErr error
	after := embedBlock.ReplaceAllFunc(before, func(block []byte) []byte {
		source := string(embedBlock.FindSubmatch(block)[1])
		content, err := os.ReadFile(source)
		if err != nil {
			renderErr = err
			return block
		}
		language := fenceLanguages[filepath.Ext(source)]
		var out bytes.Buffer
		fmt.Fprintf(&out, "<!-- embed: %s -->\n```%s\n", source, language)
		out.Write(content)
		if !bytes.HasSuffix(content, []byte("\n")) {
			out.WriteByte('\n')
		}
		out.WriteString("```\n<!-- /embed -->")
		return out.Bytes()
	})
	if renderErr != nil {
		return false, renderErr
	}
	if bytes.Equal(before, after) {
		return false, nil
	}
	if write {
		return true, os.WriteFile(file, after, 0o644)
	}
	return true, nil
}
