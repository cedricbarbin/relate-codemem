package main
import "fmt"
type Server struct { port int }
func (s *Server) Start() error { fmt.Println("x"); return nil }
func main() {}
