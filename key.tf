# Key RSA
resource "tls_private_key" "genKey" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

# Build key
resource "aws_key_pair" "deployer" {
  key_name   = "prodpai-key"
  public_key = tls_private_key.genKey.public_key_openssh
}
