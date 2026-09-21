//go:build android && cgo

package ffi

import (
	"context"
	"errors"
	"net"
	"reflect"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"golang.org/x/sys/unix"
)

func TestAndroidTXTRecords(t *testing.T) {
	name := dnsmessage.MustNewName("_dnsaddr.example.com.")
	message := dnsmessage.Message{
		Header: dnsmessage.Header{Response: true},
		Answers: []dnsmessage.Resource{
			{
				Header: dnsmessage.ResourceHeader{Name: name, Class: dnsmessage.ClassINET, Type: dnsmessage.TypeTXT},
				Body:   &dnsmessage.TXTResource{TXT: []string{"dnsaddr=", "/ip4/192.0.2.1/tcp/4501"}},
			},
			{
				Header: dnsmessage.ResourceHeader{Name: name, Class: dnsmessage.ClassINET, Type: dnsmessage.TypeTXT},
				Body:   &dnsmessage.TXTResource{TXT: []string{"dnsaddr=/ip4/192.0.2.2/tcp/4501"}},
			},
		},
	}
	response, err := message.Pack()
	if err != nil {
		t.Fatal(err)
	}
	records, err := androidTXTRecords(response)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"dnsaddr=/ip4/192.0.2.1/tcp/4501", "dnsaddr=/ip4/192.0.2.2/tcp/4501"}
	if !reflect.DeepEqual(records, want) {
		t.Fatalf("records = %v, want %v", records, want)
	}
	if _, err := androidTXTRecords([]byte{0}); err == nil {
		t.Fatal("accepted malformed DNS response")
	}
}

func TestAndroidDNSCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	resolver := androidDNSResolver{Resolver: net.DefaultResolver}
	if _, err := resolver.LookupTXT(ctx, "_dnsaddr.example.com"); !errors.Is(err, context.Canceled) {
		t.Fatalf("LookupTXT error = %v, want context.Canceled", err)
	}
	if _, err := resolver.LookupTXT(context.Background(), "example.com\x00.invalid"); err == nil {
		t.Fatal("accepted DNS name containing NUL")
	}
}

func TestAndroidDNSWait(t *testing.T) {
	descriptors, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = unix.Close(descriptors[0])
		_ = unix.Close(descriptors[1])
	})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := waitAndroidDNS(ctx, descriptors[0]); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("idle socket error = %v, want context.DeadlineExceeded", err)
	}
	if _, err := unix.Write(descriptors[1], []byte{1}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := waitAndroidDNS(ctx, descriptors[0]); err != nil {
		t.Fatalf("ready socket error = %v", err)
	}
}
